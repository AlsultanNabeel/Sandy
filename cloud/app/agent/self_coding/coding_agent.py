"""M8 — Coding agent runner.

A tool-use loop that lets Claude drive iterative coding work: search the
repo, read files, edit existing code, write new files, ask the owner when
ambiguous, and signal completion with `done`. This iterative agent loop is
used by Project Builder (SA8) for complex feature generation.

Calls go through ``code_llm.complete_with_tools`` which tries Claude (Vertex)
first and falls back to Azure GPT (chat.completions). The fallback path
translates the Anthropic tool_use/tool_result blocks into OpenAI's
tool_calls/role:tool shape and back, so the agent doesn't need to know which
provider is actually serving the call.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Set

from app.agent.self_coding.coding_agent_tools import (
    TOOL_SCHEMAS,
    AgentContext,
    dispatch_tool_call,
    short_args_log,
)
from app.integrations import claude_vertex, code_llm
from app.utils import metrics as metrics

logger = logging.getLogger(__name__)


# Sensible defaults — overridable by the caller per task.
DEFAULT_MAX_ITERATIONS = 50
DEFAULT_MAX_TOKENS_PER_CALL = 8000
# Cumulative input+output across all iterations. Each call resends the full
# message history, so the per-call input grows as the loop progresses — a
# 20-iteration run can easily cross 500K. Keep the cap generous; per-call
# token usage is bounded by max_tokens_per_call anyway.
DEFAULT_TOTAL_TOKEN_BUDGET = 800_000

# Once the conversation exceeds this many messages, drop the oldest exchanges
# (keeping the original task + the most recent N turns). Saves ~30% of cumul.
# tokens on long runs; the agent can re-read anything older it still needs.
KEEP_RECENT_TURNS = 10

# Tools that don't mutate code or signal completion. A long streak of these
# means the agent is stuck reading instead of deciding.
_EXPLORATION_TOOLS = {"read_file", "search_code", "list_tree", "get_branch_diff"}
# Nudge the agent toward action after this many consecutive exploration calls.
_NUDGE_AT_EXPLORATION_STREAK = 8

# M11: Edit-thrashing guard. When the agent keeps patching the same file the
# scope is almost always unclear and more patches won't converge — the right
# move is to wrap up or ask the owner. Counts apply_patch + write_new_file
# touches per path within a single run.
_THRASH_TOOLS = {"apply_patch", "write_new_file"}
_THRASH_NUDGE_AT = 6
_THRASH_ABORT_AT = 10

# Prefixes the owner uses to deny an ask_owner request. Anything else is
# treated as approval — keeps a casual "اه" / "yes" working without making
# the agent parse the answer.
_NEGATIVE_ANSWER_PREFIXES = (
    "لا", "لاء", "لأ", "كلا", "كنسل",
    "الغي", "إلغي", "الغاء", "إلغاء", "الغ",
    "no", "nope", "n", "stop", "cancel", "abort",
    "توقف", "وقف", "ايقاف",
)

# M15: when a feature stalls, the owner can short-circuit with one of these
# replies to force the agent to wrap up at its current state (call done with
# whatever was applied) instead of digging further.
_WRAP_UP_ANSWER_PREFIXES = (
    "خلصي", "خلصى", "خلّصي", "خلصت",
    "خلاص", "كفاية", "كفى",
    "done", "finish", "wrap", "ship",
)


def _is_negative_answer(answer: str) -> bool:
    a = (answer or "").strip().lower()
    if not a:
        return True  # no reply, so don't auto-approve
    return any(a == p or a.startswith(p + " ") for p in _NEGATIVE_ANSWER_PREFIXES)


def _is_wrap_up_answer(answer: str) -> bool:
    a = (answer or "").strip().lower()
    if not a:
        return False
    return any(a == p or a.startswith(p + " ") for p in _WRAP_UP_ANSWER_PREFIXES)


def _build_stall_question(
    *,
    kind: str,
    detail: str,
    files_created: List[str],
    files_patched: List[str],
) -> str:
    """Build the stall-suspension question shown to the owner.

    `kind` is a short tag ('exploration' or 'thrashing') and `detail` is
    a one-line reason (e.g. file path plus a count). The notifier layer
    wraps this with the branch link and the wrap-up/cancel options."""
    lines = [f"🛑 وقفت ({kind}): {detail}"]
    applied: List[str] = []
    seen: Set[str] = set()
    for f in list(files_created) + list(files_patched):
        if isinstance(f, str) and f and f not in seen:
            applied.append(f)
            seen.add(f)
    if applied:
        lines.append("")
        lines.append("ما تم لحد الآن:")
        for f in applied[:10]:
            lines.append(f"  ✅ `{f}`")
        if len(applied) > 10:
            lines.append(f"  … و{len(applied) - 10} ملف ثاني")
    lines.append("")
    lines.append("شو بدك أعمل؟")
    lines.append("- `خلّصي` — اقفلي الفيتشر بما تم، افتحي PR.")
    lines.append("- `الغي` — احذفي اللي تم.")
    lines.append("- توضيح حر — أكمّل بناءً عليه.")
    return "\n".join(lines)


def _suspend_state(
    *,
    messages: List[Dict[str, Any]],
    ctx: AgentContext,
    iterations: int,
    total_in: int,
    total_out: int,
    stall: bool = False,
) -> Dict[str, Any]:
    """Build the resume_state dict used by both ask_owner and stall paths."""
    return {
        "messages": messages,
        "tool_results_pending": [],
        "pending_tool_use_id": "",
        "iterations": iterations,
        "in_tokens": total_in,
        "out_tokens": total_out,
        "file_read_shas": dict(ctx.file_read_shas),
        "approved_paths": sorted(ctx.approved_paths),
        "pending_paths_to_approve": list(ctx.pending_paths_to_approve),
        "stall": stall,
    }
# Abort the run after this many — burning more iterations won't help.
_ABORT_AT_EXPLORATION_STREAK = 15


def run_agent(
    *,
    task_description: str,
    ctx: AgentContext,
    system_prompt: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_tokens_per_call: int = DEFAULT_MAX_TOKENS_PER_CALL,
    total_token_budget: int = DEFAULT_TOTAL_TOKEN_BUDGET,
    on_tool_call: Optional[Callable[[str, Dict[str, Any], str], None]] = None,
    resume_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Drive the agent loop until terminal or budget exceeded.

    Args:
        task_description: the owner's request (Arabic OK)
        ctx: shared AgentContext — wrappers mutate it as they run
        system_prompt: instructions describing the role, conventions, and
            any wiring requirements (caller builds this — see step 5)
        on_tool_call: optional callback fired per tool call with
            (name, arguments, result_string). Use it to push live updates
            to Telegram.
        resume_state: when resuming after `ask_owner`, pass the dict that
            was returned with `suspended=True` plus the owner's answer in
            `resume_state['owner_answer']`. The runner appends the answer
            as a tool_result and continues.

    Returns one of:
        Done    → {ok: True, done: True, summary, iterations, usage}
        Suspend → {ok: True, suspended: True, question, iterations,
                   resume_state: {messages, tool_results_pending,
                                  pending_tool_use_id, iterations,
                                  in_tokens, out_tokens}}
        Failure → {ok: False, error, iterations, usage}
    """
    if not (claude_vertex.is_available() or code_llm.is_available()):
        return {
            "ok": False,
            "error": "ولا LLM متاح (لا Claude ولا Azure) — لا يمكن تشغيل agent loop",
            "iterations": 0,
        }

    if resume_state:
        messages: List[Dict[str, Any]] = list(resume_state.get("messages") or [])
        pending_results = list(resume_state.get("tool_results_pending") or [])
        pending_tool_use_id = resume_state.get("pending_tool_use_id") or ""
        iterations = int(resume_state.get("iterations") or 0)
        total_in = int(resume_state.get("in_tokens") or 0)
        total_out = int(resume_state.get("out_tokens") or 0)
        ctx.file_read_shas.update(resume_state.get("file_read_shas") or {})
        owner_answer = (resume_state.get("owner_answer") or "").strip()
        is_stall_resume = bool(resume_state.get("stall"))

        # Restore approvals granted earlier in this task.
        for p in resume_state.get("approved_paths") or []:
            ctx.approved_paths.add(p)

        # M15: stall resume — owner can short-circuit with 'خلّصي' (wrap up
        # with what was applied) or 'الغي' (cancel). Otherwise the answer
        # is treated as a fresh clarification and the loop continues.
        if is_stall_resume:
            if _is_negative_answer(owner_answer):
                return {
                    "ok": False,
                    "error": "cancelled by owner after stall",
                    "iterations": iterations,
                    "usage": {"input_tokens": total_in, "output_tokens": total_out},
                }
            if _is_wrap_up_answer(owner_answer):
                # Synthesize a done — give the higher layer what it needs to
                # open a PR with whatever was applied.
                return {
                    "ok": True,
                    "done": True,
                    "summary": (
                        "وقفت بطلب الأونر بعد stall — تم تطبيق "
                        f"{len(ctx.files_created) + len(ctx.files_patched)} ملف."
                    ),
                    "iterations": iterations,
                    "usage": {"input_tokens": total_in, "output_tokens": total_out},
                    "files_created": list(ctx.files_created),
                    "files_patched": list(ctx.files_patched),
                    "files_read": list(ctx.files_read),
                }
            # Free-text clarification — feed as a plain user message and let
            # the loop continue from there.
            messages.append({
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": (
                        "💬 توضيح من الأونر بعد ما وقفت: "
                        f"{owner_answer or '(بدون رد)'}.\n"
                        "كملي بناءً على هاد، أو نادي done لو الشغل خلص."
                    ),
                }],
            })
        else:
            # If the agent's ask_owner declared `paths_to_approve` and the
            # answer isn't a refusal, promote them so the next write/patch on
            # those paths doesn't hit "نادي ask_owner أولاً" and re-suspend.
            pending_paths = list(resume_state.get("pending_paths_to_approve") or [])
            approval_note = ""
            if pending_paths and not _is_negative_answer(owner_answer):
                for p in pending_paths:
                    ctx.approved_paths.add(p)
                approval_note = (
                    "\n\n✅ تمت الموافقة على المسارات التالية لباقي هاي الـ task: "
                    + ", ".join(pending_paths)
                    + ". تقدري تستخدمي write_new_file / apply_patch عليها مباشرة "
                    "بدون ما تسألي مرة ثانية."
                )

            if pending_tool_use_id:
                # Defense in depth: drop any prior placeholder so we don't end
                # up with two tool_results for the same id (Claude rejects that).
                pending_results = [
                    r for r in pending_results
                    if r.get("tool_use_id") != pending_tool_use_id
                ]
                pending_results.append({
                    "type": "tool_result",
                    "tool_use_id": pending_tool_use_id,
                    "content": (
                        f"الأونر رد: {owner_answer or '(بدون رد)'}{approval_note}"
                    ),
                })
            if pending_results:
                messages.append({"role": "user", "content": pending_results})

        # Clear the suspension flags so the next loop turn doesn't re-suspend
        ctx.pending_owner_question = None
        ctx.pending_paths_to_approve = []
        recent_errors: List[str] = []
        exploration_streak = 0
        nudge_injected = False
        stop_nudge_used = False
        patches_per_file: Dict[str, int] = {}
        thrash_nudge_files: Set[str] = set()
    else:
        messages = [{"role": "user", "content": task_description}]
        iterations = 0
        total_in = 0
        total_out = 0
        recent_errors = []
        exploration_streak = 0
        nudge_injected = False
        stop_nudge_used = False
        patches_per_file = {}
        thrash_nudge_files: Set[str] = set()

    while iterations < max_iterations:
        iterations += 1

        if total_in + total_out > total_token_budget:
            return {
                "ok": False,
                "error": (
                    f"token budget exceeded: {total_in + total_out} > "
                    f"{total_token_budget}"
                ),
                "iterations": iterations,
                "usage": {"input_tokens": total_in, "output_tokens": total_out},
            }

        resp = code_llm.complete_with_tools(
            system=system_prompt,
            messages=messages,
            tools=TOOL_SCHEMAS,
            max_tokens=max_tokens_per_call,
        )
        if not resp.get("ok"):
            return {
                "ok": False,
                "error": resp.get("error") or "Claude tool-use call failed",
                "iterations": iterations,
                "usage": {"input_tokens": total_in, "output_tokens": total_out},
            }

        usage = resp.get("usage") or {}
        total_in += int(usage.get("input_tokens") or 0)
        total_out += int(usage.get("output_tokens") or 0)

        content_blocks: List[Dict[str, Any]] = resp.get("content_blocks") or []
        messages.append({"role": "assistant", "content": content_blocks})

        tool_uses = [b for b in content_blocks if b.get("type") == "tool_use"]
        if not tool_uses:
            text_only = " ".join(
                b.get("text", "") for b in content_blocks if b.get("type") == "text"
            ).strip()
            # One-shot recovery: the agent often "narrates" completion instead
            # of calling `done`. Nudge it once before failing the task.
            if not stop_nudge_used:
                stop_nudge_used = True
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "text",
                        "text": (
                            "⚠️ ما استدعيتي أي tool. أنا runner — مش بقدر "
                            "أكمل من غير tool call. لو خلصتي الفيتشر نادي "
                            "`done(summary=\"...\")`. لو لسا في شغل، نادي "
                            "الـ tool المناسب (write_new_file / apply_patch / "
                            "read_file / ask_owner). لا ترجعي نص بدون tool."
                        ),
                    }],
                })
                continue
            return {
                "ok": False,
                "error": (
                    "agent stopped without calling a tool (twice). آخر نص: "
                    + (text_only[:300] or "(فاضي)")
                ),
                "iterations": iterations,
                "usage": {"input_tokens": total_in, "output_tokens": total_out},
            }

        tool_results: List[Dict[str, Any]] = []
        ask_owner_tool_use_id: str = ""
        done_signal = False
        for tool_use in tool_uses:
            name = tool_use.get("name", "")
            args = tool_use.get("input") or {}

            if name in _EXPLORATION_TOOLS:
                exploration_streak += 1
            else:
                exploration_streak = 0
                nudge_injected = False

            result_text = dispatch_tool_call(name, args, ctx)

            # M11: edit-thrashing accounting. Only count successful writes —
            # ERROR results don't represent real changes to the same file.
            if (
                name in _THRASH_TOOLS
                and not result_text.startswith("ERROR:")
            ):
                touched_path = str(args.get("path") or "").strip()
                if touched_path:
                    patches_per_file[touched_path] = (
                        patches_per_file.get(touched_path, 0) + 1
                    )
                    if patches_per_file[touched_path] >= _THRASH_ABORT_AT:
                        # M15: convert to stall suspension instead of failure
                        stall_q = _build_stall_question(
                            kind="thrashing",
                            detail=(
                                f"`{touched_path}` اتعدّل "
                                f"{patches_per_file[touched_path]} مرات في "
                                "نفس الفيتشر — الـ scope غامض."
                            ),
                            files_created=list(ctx.files_created),
                            files_patched=list(ctx.files_patched),
                        )
                        return {
                            "ok": True,
                            "suspended": True,
                            "question": stall_q,
                            "iterations": iterations,
                            "usage": {"input_tokens": total_in, "output_tokens": total_out},
                            "resume_state": _suspend_state(
                                messages=messages, ctx=ctx,
                                iterations=iterations,
                                total_in=total_in, total_out=total_out,
                                stall=True,
                            ),
                        }

            if on_tool_call is not None:
                try:
                    on_tool_call(name, args, result_text)
                except Exception as exc:
                    logger.debug("[coding_agent] on_tool_call hook failed: %s", exc)
            logger.info("[coding_agent] iter=%d %s", iterations, short_args_log(name, args))

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.get("id", ""),
                "content": result_text,
            })
            try:
                metrics.inc_self_coding_tool_call(name)
            except Exception:
                pass

            if result_text.startswith("ERROR:"):
                recent_errors.append(result_text)
                if len(recent_errors) > 3:
                    recent_errors = recent_errors[-3:]
                if (
                    len(recent_errors) == 3
                    and recent_errors[0] == recent_errors[1] == recent_errors[2]
                ):
                    return {
                        "ok": False,
                        "error": (
                            "agent عالقة في خطأ متكرر: "
                            + recent_errors[-1][:200]
                        ),
                        "iterations": iterations,
                        "usage": {"input_tokens": total_in, "output_tokens": total_out},
                    }
            else:
                recent_errors = []

            if name == "done":
                done_signal = True
                break
            if name == "ask_owner":
                ask_owner_tool_use_id = tool_use.get("id", "")

        if done_signal:
            try:
                metrics.observe_self_coding_iterations(iterations)
                metrics.observe_self_coding_tokens(total_in + total_out)
            except Exception:
                pass
            return {
                "ok": True,
                "done": True,
                "summary": ctx.done_summary or "خلصت.",
                "iterations": iterations,
                "usage": {"input_tokens": total_in, "output_tokens": total_out},
                "files_created": list(ctx.files_created),
                "files_patched": list(ctx.files_patched),
                "files_read": list(ctx.files_read),
            }

        if ctx.pending_owner_question and ask_owner_tool_use_id:
            # Strip the SUSPENDED placeholder for ask_owner — the resume path
            # injects the real owner answer for this tool_use_id.
            tool_results_for_resume = [
                r for r in tool_results
                if r.get("tool_use_id") != ask_owner_tool_use_id
            ]
            return {
                "ok": True,
                "suspended": True,
                "question": ctx.pending_owner_question,
                "iterations": iterations,
                "usage": {"input_tokens": total_in, "output_tokens": total_out},
                "resume_state": {
                    "messages": messages,
                    "tool_results_pending": tool_results_for_resume,
                    "pending_tool_use_id": ask_owner_tool_use_id,
                    "iterations": iterations,
                    "in_tokens": total_in,
                    "out_tokens": total_out,
                    "file_read_shas": dict(ctx.file_read_shas),
                    "approved_paths": sorted(ctx.approved_paths),
                    "pending_paths_to_approve": list(
                        ctx.pending_paths_to_approve
                    ),
                },
            }

        if exploration_streak >= _ABORT_AT_EXPLORATION_STREAK:
            # M15: convert the hard abort into a stall suspension so the
            # owner gets a question + the option to wrap up with what was
            # applied, instead of a flat failure.
            stall_q = _build_stall_question(
                kind="exploration",
                detail=(
                    f"{exploration_streak} reads متتالية على الفيتشر بدون "
                    "ما يتضح المعيار اللي يخلّصه."
                ),
                files_created=list(ctx.files_created),
                files_patched=list(ctx.files_patched),
            )
            return {
                "ok": True,
                "suspended": True,
                "question": stall_q,
                "iterations": iterations,
                "usage": {"input_tokens": total_in, "output_tokens": total_out},
                "resume_state": _suspend_state(
                    messages=messages, ctx=ctx, iterations=iterations,
                    total_in=total_in, total_out=total_out, stall=True,
                ),
            }

        user_content: List[Dict[str, Any]] = list(tool_results)
        if (
            exploration_streak >= _NUDGE_AT_EXPLORATION_STREAK
            and not nudge_injected
        ):
            user_content.append({
                "type": "text",
                "text": (
                    "⚠️ توقفي عن الاستكشاف — قرأتي ملفات كفاية. لو لسا "
                    "الـ scope مش واضح، السبب الأرجح إن الـ feature غامضة، "
                    "والتخمين رح يضيع reads أكثر. الخطوة التالية:\n"
                    "(أ) **`ask_owner`** بسؤال محدد عن الـ output المتوقع "
                    "— هاد الأسرع لو ضايعة.\n"
                    "(ب) `write_new_file` / `apply_patch` لو فعلاً صار "
                    "الـ scope واضح.\n"
                    "(ج) `done(summary)` لو الشغل فعلاً خلص."
                ),
            })
            nudge_injected = True

        # M11: thrashing nudge — once per file. Tell the agent it's editing
        # the same path too many times and to wrap up or ask for clarity.
        for path, count in patches_per_file.items():
            if count >= _THRASH_NUDGE_AT and path not in thrash_nudge_files:
                user_content.append({
                    "type": "text",
                    "text": (
                        f"⚠️ عدّلتي `{path}` {count} مرات في هاد الفيتشر — "
                        "هاد مؤشّر إن الـ scope مش واضح، مش إن الكود محتاج "
                        "مزيد من التنقيح. الخطوة التالية:\n"
                        "(أ) `done(summary)` إذا الشغل فعلاً مقبول.\n"
                        "(ب) `ask_owner` إذا الـ output المطلوب لسا غامض."
                    ),
                })
                thrash_nudge_files.add(path)

        messages.append({"role": "user", "content": user_content})

        # Prune older history once we cross the threshold. Keep the original
        # task message + the last 2*KEEP_RECENT_TURNS messages (each turn =
        # one assistant + one user message). Slice carefully so we don't break
        # tool_use/tool_result pairing.
        max_messages = 1 + 2 * KEEP_RECENT_TURNS
        if len(messages) > max_messages:
            tail_start = len(messages) - 2 * KEEP_RECENT_TURNS
            # Tail must start with an assistant message so each tool_use has
            # its matching tool_result in the kept slice.
            while tail_start < len(messages) and messages[tail_start].get("role") != "assistant":
                tail_start += 1
            if tail_start < len(messages):
                messages = [messages[0]] + messages[tail_start:]

    return {
        "ok": False,
        "error": f"max iterations ({max_iterations}) reached without done",
        "iterations": iterations,
        "usage": {"input_tokens": total_in, "output_tokens": total_out},
    }
