"""Web research orchestration — entry point for Sandy's research feature.

Delegates to:
  research_intent.py   — query classification
  research_pipeline.py — data extraction, dedup, filter, rank
  research_formatter.py — Arabic result formatting

Public API (also re-exported for backward compat):
  execute_web_research(query, user_message, ...) -> (str, list)
"""

import os
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from app.agent.deep_context import (
    LAST_SEARCH_RESULTS_KEY,
    places_to_search_items,
    wants_comparison_grounded_in_search,
)
from app.features.research_intent import (  # noqa: F401
    detect_research_type,
    extract_requested_result_count,
    is_research_request,
    is_research_followup_request,
)
from app.features.google_places import format_places_for_reply, search_places
from app.features.research_pipeline import (  # noqa: F401
    run_research_pipeline,
    deduplicate_research_results,
    is_official_source_url,
)
from app.features.research_formatter import (  # noqa: F401
    summarize_research_results,
)


def _research_context_items_from_exa(
    rows: List[Dict[str, Any]], limit: int
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, r in enumerate(rows[:limit], 1):
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snip = (r.get("text") or "").strip()[:520]
        label = title or url or snip[:80] or ""
        if not label:
            continue
        out.append({"row": i, "title": label[:440], "url": url[:400], "snippet": snip})
    return out


def _research_context_items_from_pipeline(
    results: List[Dict[str, Any]], limit: int
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(results[:limit], 1):
        if not isinstance(item, dict):
            continue
        pd = item.get("page_data") or {}
        title_bit = (
            pd.get("program_name")
            or pd.get("product_name")
            or pd.get("place_name")
            or pd.get("headline")
            or pd.get("title")
            or item.get("source_title")
            or ""
        )
        inst = pd.get("institution_name") or ""
        bits = [
            str(x).strip()
            for x in (inst, str(title_bit or "").strip())
            if str(x).strip()
        ]
        label = (
            " — ".join(bits)
            if bits
            else (item.get("source_title") or item.get("source_url") or "بدون عنوان")
        )
        url = str(item.get("source_url") or "").strip()
        snip = str(item.get("exa_snippet") or "").strip()[:520]
        out.append(
            {"row": i, "title": str(label)[:440], "url": url[:400], "snippet": snip}
        )
    return out


def execute_web_research(
    query: str,
    user_message: str,
    research_type: str = "general",
    requested_count: int = 5,
    search_exa_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    get_exa_page_content_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    create_chat_completion_fn: Optional[Callable[..., Any]] = None,
    exa_api_key: str = "",
    session: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Run web research. Returns (Arabic reply, structured items for session buffer)."""

    sess = session if isinstance(session, dict) else {}

    def _last_search_payload() -> Dict[str, Any]:
        payload = sess.get(LAST_SEARCH_RESULTS_KEY) if isinstance(sess, dict) else {}
        return payload if isinstance(payload, dict) else {}

    last_search_payload = _last_search_payload()
    has_last_search = bool(last_search_payload.get("items"))
    followup_requested = is_research_followup_request(user_message) or (
        has_last_search and wants_comparison_grounded_in_search(user_message)
    )

    def _format_followup_reply(
        payload: Dict[str, Any],
    ) -> Tuple[str, List[Dict[str, Any]]]:
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        clean_items = [
            it
            for it in items
            if isinstance(it, dict)
            and (it.get("title") or it.get("snippet") or it.get("url"))
        ]
        if not clean_items:
            return "ما عندي نتائج سابقة أقدر أبني عليها. اعيدي البحث أولاً.", []

        best = clean_items[0]
        best_title = str(
            best.get("title") or best.get("url") or "النتيجة الأولى"
        ).strip()
        best_snippet = str(best.get("snippet") or "").strip()
        reply_lines = [f"استنادًا للنتائج السابقة، الأفضل مبدئيًا هو: {best_title}"]
        if best_snippet:
            reply_lines.append(f"السبب/الملخص: {best_snippet[:220]}")

        if len(clean_items) > 1:
            reply_lines.append("خيارات قريبة:")
            for idx, item in enumerate(clean_items[1:4], 2):
                title = str(
                    item.get("title") or item.get("url") or f"الخيار {idx}"
                ).strip()
                reply_lines.append(f"{idx}. {title}")

        return "\n".join(reply_lines), clean_items[: max(3, requested_count)]

    if research_type == "places":
        places_api_key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
        if not places_api_key:
            return "خدمة الأماكن غير متوفرة حالياً.", []

        try:
            places = search_places(
                query, places_api_key, max_results=max(1, min(requested_count, 8))
            )
        except Exception as e:
            print(f"[Research] places call raised: {e}", flush=True)
            return f"ما قدرت أجد أماكن عن '{query}' الآن.", []

        if not places:
            return f"ما قدرت أجد أماكن واضحة عن '{query}'.", []

        ctx_items = places_to_search_items(places, limit=max(requested_count, 8))
        return format_places_for_reply(places), ctx_items

    if followup_requested:
        followup_reply, followup_items = _format_followup_reply(last_search_payload)
        if followup_items:
            return followup_reply, followup_items

    if not search_exa_fn or not exa_api_key:
        return "ما قدرت أكمل البحث — خدمة البحث غير متوفرة حالياً.", []

    def _format_fast_path_reply(body: str) -> str:
        body = str(body or "").strip()
        if not body:
            body = "ما قدرت ألخص النتائج بشكل واضح حالياً."
        source_lines = []
        for idx, url in enumerate(sources[:5], 1):
            source_lines.append(f"{idx}. {_short_url(url)}")
        if source_lines:
            return f"📌 الملخص:\n{body}\n\n📎 المصادر:\n" + "\n".join(source_lines)
        return f"📌 الملخص:\n{body}"

    # Fast path for news/general: Exa snippets plus an AI summary.
    if research_type in {"news", "general"}:
        try:
            results = search_exa_fn(query, exa_api_key=exa_api_key, num_results=8)
        except Exception as e:
            print(f"[Research] Exa call raised: {e}", flush=True)
            return f"ما قدرت أجد نتائج عن '{query}' — خطأ في الاتصال بخدمة البحث.", []

        ctx_items = _research_context_items_from_exa(results, max(requested_count, 8))

        if not results:
            return f"ما قدرت أجد نتائج عن '{query}' الآن. جرب مرة ثانية لاحقاً.", []

        snippets: List[str] = []
        sources: List[str] = []
        for r in results[:8]:
            title = (r.get("title") or "").strip()
            text = (r.get("text") or "")[:400].strip()
            url = (r.get("url") or "").strip()
            line = f"- {title}" if title else ""
            if text:
                line += f": {text}" if line else f"- {text}"
            if line:
                snippets.append(line)
                if url:
                    sources.append(url)

        if not snippets:
            return f"ما قدرت أجد نتائج واضحة عن '{query}'.", ctx_items

        def _short_url(u: str) -> str:
            try:
                parsed = urlparse(u)
                return parsed.netloc.removeprefix("www.") or u
            except Exception:
                return u

        if create_chat_completion_fn is None:
            return (
                _format_fast_path_reply("\n".join(snippets[:requested_count])),
                ctx_items,
            )

        try:
            response = create_chat_completion_fn(
                temperature=0.3,
                max_tokens=600,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "أنت ساندي، مساعدة ذكية. لخّص نتائج البحث باللغة العربية "
                            "بشكل مختصر وواضح ومرتب حسب طلب المستخدم. "
                            "لا تبدأ بـ'تفضل' أو كلمات فارغة — ابدأ مباشرة بالمعلومات."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"طلب المستخدم: {user_message}\n\n"
                            f"نتائج البحث:\n" + "\n".join(snippets)
                        ),
                    },
                ],
            )
            reply = (response.choices[0].message.content or "").strip()
        except Exception as e:
            print(f"[Research] AI summary failed: {e}", flush=True)
            reply = ""

        base = reply if reply else "\n".join(snippets[:requested_count])
        return _format_fast_path_reply(base), ctx_items

    # Heavy path for education/product/travel: run the full pipeline.
    try:
        results = run_research_pipeline(
            user_query=query,
            research_type=research_type,
            requested_count=requested_count,
            search_exa_fn=search_exa_fn,
            get_exa_page_content_fn=get_exa_page_content_fn,
            create_chat_completion_fn=create_chat_completion_fn,
            exa_api_key=exa_api_key,
        )
    except Exception as e:
        print(f"[Research] pipeline failed: {e}", flush=True)
        return f"ما قدرت أجد نتائج عن '{query}' — حدث خطأ غير متوقع.", []

    if not results:
        return f"ما قدرت أجد نتائج واضحة عن '{query}'.", []

    ctx_heavy = _research_context_items_from_pipeline(results, requested_count)
    summarized = summarize_research_results(results, requested_count)
    return summarized, ctx_heavy
