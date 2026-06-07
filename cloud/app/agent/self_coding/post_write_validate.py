"""In-process content validators for self-coding writes.

Replaces the previous subprocess-based approach that depended on
`scripts/post_edit_validate.py` — which was removed from the repo in
b942eb5 and never restored. The script-based design was also doubly
broken: the worker filesystem has no checkout of the target repo,
so validating LOCAL paths after a remote GitHub write could never
have worked.

The new model runs cheap structural checks on the proposed `content`
**before** it's sent to GitHub. This catches the classes of bug we've
actually seen blow up CI — Python syntax errors, malformed JSON/YAML,
broken `@pytest.mark.parametrize` decorators, empty function bodies,
trailing whitespace — without needing any local copy of the file and
without a subprocess hop.

Callers (repo_create / repo_patch) construct the full new content
locally anyway, so the input is always in hand.
"""

from __future__ import annotations

import ast
import builtins as _py_builtins
import json
import logging
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# Cap the number of issues reported per call — long lists overwhelm the
# agent's tool_result and waste tokens. The first few are enough to act on.
_MAX_REPORTED_ISSUES = 5

# Cached so we don't rebuild on every validate_content call.
_PY_BUILTINS: frozenset[str] = frozenset(dir(_py_builtins))


# Branch file-list cache, used by the local-import check.
# Maps (repo, branch) -> (timestamp, set of paths). Populated lazily by
# `_get_repo_files`; each successful write extends the cached set via
# `mark_path_written` so a freshly-created file is "seen" immediately by
# subsequent validations in the same task.
_BRANCH_FILES_TTL_S = 3600  # cap memory growth from short-lived tasks
_branch_files_cache: Dict[Tuple[Optional[str], str], Tuple[float, Set[str]]] = {}
_branch_files_lock = threading.Lock()


def validate_content(
    path: str,
    content: str,
    *,
    repo: Optional[str] = None,
    branch: Optional[str] = None,
) -> Tuple[bool, str]:
    """Cheap structural check on a file's proposed full content.

    Returns:
        (True, "")          → content is OK, or the file type is one we
                              don't have a parser for.
        (False, message)    → content must not be written; `message`
                              describes why and includes line/col when
                              the parser provides it.

    Permissive on empty / whitespace-only content — files like
    `__init__.py` or `.gitkeep` are legitimately empty.

    When `branch` is supplied, Python files also get a local-import
    existence check against the file list of that branch (cached). Pass
    `repo=None` for the default repo. The check is skipped silently if
    the branch tree can't be fetched.
    """
    if not isinstance(content, str):
        return False, f"{path}: content must be str, got {type(content).__name__}"
    if "\x00" in content:
        return False, f"{path}: contains null bytes (looks binary)"
    if not content:
        return True, ""

    lower = path.lower()
    if lower.endswith(".py"):
        try:
            compile(content, path, "exec")
        except SyntaxError as exc:
            return False, (
                f"{path}: SyntaxError line {exc.lineno} col {exc.offset}: "
                f"{exc.msg}"
            )
        except ValueError as exc:
            # e.g. "source code string cannot contain null bytes" (already
            # caught above) or other low-level rejections from compile().
            return False, f"{path}: invalid Python source: {exc}"
        # Structural sanity that compile() doesn't catch — empty bodies,
        # malformed @pytest.mark.parametrize, test fns that are just `pass`.
        ok, msg = _validate_python_ast(content, path)
        if not ok:
            return False, msg
        # NameError-style mistakes: bare `except:` and type annotations
        # referencing names that aren't imported or defined.
        ok, msg = _validate_python_names(content, path)
        if not ok:
            return False, msg
        # Catches ruff W291 (trailing whitespace) and W292 (no final
        # newline) before CI does. Cheap text-level pass.
        ok, msg = _validate_text_hygiene(content, path)
        if not ok:
            return False, msg
        # Local-import existence — flag `from app.X.Y import Z` when
        # `app/X/Y.py` (or its __init__.py) doesn't exist on the branch.
        # Catches "agent imports a module it forgot to create" before
        # the GitHub round-trip, before CI, before the agent loops on
        # ImportError. Only runs when we have branch context.
        if branch:
            repo_files = _get_repo_files(repo, branch)
            if repo_files is not None:
                ok, msg = _validate_local_imports(content, path, repo_files)
                if not ok:
                    return False, msg
    elif lower.endswith(".json"):
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            return False, (
                f"{path}: invalid JSON line {exc.lineno} col {exc.colno}: "
                f"{exc.msg}"
            )
    elif lower.endswith((".yml", ".yaml")):
        try:
            import yaml  # type: ignore
        except ImportError:
            # PyYAML is optional — if missing, skip rather than fail.
            return True, ""
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as exc:  # type: ignore[attr-defined]
            return False, f"{path}: invalid YAML: {exc}"

    return True, ""


def _validate_python_ast(content: str, path: str) -> Tuple[bool, str]:
    """AST-level structural checks for Python files.

    Catches problems that compile-checks miss but break CI immediately:
      • Function / class with an empty body (no `pass`, no statements —
        compile() actually allows this in some edge cases but it usually
        means the agent forgot to add the body).
      • `@pytest.mark.parametrize` missing the second argument
        (the parameter values). Pytest refuses to collect the file.
      • A test function whose entire body is just `pass` — that test
        will run but assert nothing, which most CI configs flag.

    Returns (False, msg) only when we're confident the issue would fail
    CI; otherwise (True, "").
    """
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError:
        # compile() in the caller already reported this — don't double up.
        return True, ""

    issues: List[str] = []
    lower = path.lower()
    is_test_file = (
        "/tests/" in lower
        or "/test_" in lower
        or lower.split("/")[-1].startswith("test_")
        or lower.endswith("_test.py")
    )

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.body:
                issues.append(
                    f"line {node.lineno}: function `{node.name}` has empty body"
                )
            elif (
                is_test_file
                and node.name.startswith("test_")
                and len(node.body) == 1
                and isinstance(node.body[0], ast.Pass)
            ):
                issues.append(
                    f"line {node.lineno}: test `{node.name}` body is only "
                    "`pass` — no assertions, CI usually flags this"
                )
            for dec in node.decorator_list:
                derr = _check_decorator_shape(dec, node.name)
                if derr:
                    issues.append(derr)
        elif isinstance(node, ast.ClassDef):
            if not node.body:
                issues.append(
                    f"line {node.lineno}: class `{node.name}` has empty body"
                )

        if len(issues) >= _MAX_REPORTED_ISSUES:
            break

    if issues:
        head = f"{path}: structural issues:"
        return False, head + "\n  - " + "\n  - ".join(issues[:_MAX_REPORTED_ISSUES])
    return True, ""


def _validate_python_names(content: str, path: str) -> Tuple[bool, str]:
    """Catch NameError-style mistakes that fail at runtime / mypy.

    Two checks:
      1. Bare `except:` clauses (ruff E722 — also swallows KeyboardInterrupt
         and SystemExit).
      2. Type annotations referencing names that aren't imported, aren't
         module-level definitions, and aren't Python builtins. Skipped
         entirely when `from __future__ import annotations` is present
         because annotations become strings and won't NameError.

    Both checks are intentionally conservative — they only fire when the
    name is unmistakably unresolved. Star imports short-circuit the
    annotation check (can't reason about what `*` brought in).
    """
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError:
        return True, ""  # already reported by compile() upstream

    issues: List[str] = []

    # 1) Bare except
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append(
                f"line {node.lineno}: bare `except:` — name a specific "
                "exception (or at minimum `Exception`). Bare except swallows "
                "KeyboardInterrupt/SystemExit and trips ruff E722."
            )
            if len(issues) >= _MAX_REPORTED_ISSUES:
                break

    # 2) Annotation name resolution — skip if PEP 563 is in effect.
    has_future_annotations = any(
        isinstance(n, ast.ImportFrom)
        and n.module == "__future__"
        and any(a.name == "annotations" for a in n.names)
        for n in tree.body
    )

    if not has_future_annotations and len(issues) < _MAX_REPORTED_ISSUES:
        # Gather names that could be referenced from annotations.
        known: Set[str] = set(_PY_BUILTINS)
        star_import_seen = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    # `import a.b.c` exposes `a`; `import a.b as c` exposes `c`
                    known.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        star_import_seen = True
                        continue
                    known.add(alias.asname or alias.name)
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                known.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        known.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(
                node.target, ast.Name
            ):
                known.add(node.target.id)

        if not star_import_seen:
            seen_issue: Set[Tuple[int, str]] = set()
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                anns: List[ast.expr] = []
                args = node.args
                for arg in (
                    (getattr(args, "posonlyargs", None) or [])
                    + (args.args or [])
                    + (args.kwonlyargs or [])
                ):
                    if arg.annotation is not None:
                        anns.append(arg.annotation)
                if args.vararg and args.vararg.annotation is not None:
                    anns.append(args.vararg.annotation)
                if args.kwarg and args.kwarg.annotation is not None:
                    anns.append(args.kwarg.annotation)
                if node.returns is not None:
                    anns.append(node.returns)

                for ann in anns:
                    for sub in ast.walk(ann):
                        if not isinstance(sub, ast.Name):
                            continue
                        if sub.id in known:
                            continue
                        key = (ann.lineno, sub.id)
                        if key in seen_issue:
                            continue
                        seen_issue.add(key)
                        issues.append(
                            f"line {ann.lineno}: annotation in `{node.name}` "
                            f"references `{sub.id}` which isn't imported or "
                            "defined — would NameError at runtime. Add the "
                            "import, or use `from __future__ import "
                            "annotations` if it's intentionally forward."
                        )
                        if len(issues) >= _MAX_REPORTED_ISSUES:
                            break
                    if len(issues) >= _MAX_REPORTED_ISSUES:
                        break
                if len(issues) >= _MAX_REPORTED_ISSUES:
                    break

    if issues:
        return False, (
            f"{path}: name resolution issues:\n  - "
            + "\n  - ".join(issues[:_MAX_REPORTED_ISSUES])
        )
    return True, ""


def _check_decorator_shape(dec: ast.expr, fn_name: str) -> Optional[str]:
    """Validate well-known decorator call shapes.

    Currently only `@pytest.mark.parametrize(...)` — that's the one whose
    misuse has actually broken CI in past tasks (overlapping patches that
    dropped the values list).
    """
    if not isinstance(dec, ast.Call):
        return None
    # Reconstruct the dotted decorator name (e.g. "pytest.mark.parametrize")
    parts: List[str] = []
    node: Optional[ast.expr] = dec.func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    target_name = ".".join(reversed(parts))

    if target_name in ("pytest.mark.parametrize", "parametrize"):
        n_pos = len(dec.args)
        n_kw = len(dec.keywords or [])
        if n_pos + n_kw < 2:
            return (
                f"line {dec.lineno}: @{target_name} on `{fn_name}` needs "
                f"≥2 arguments (got {n_pos + n_kw}); pytest will refuse "
                "to collect the file"
            )
    return None


def _validate_text_hygiene(content: str, path: str) -> Tuple[bool, str]:
    """Cheap text checks that ruff/CI commonly enforce.

    Returns (False, msg) for:
      • W292: file doesn't end with a newline.
      • W291: trailing whitespace on any line.

    These are unambiguous and the agent can fix them mechanically when
    we report them.
    """
    # endswith("\n") matches both LF ("\n") and CRLF ("\r\n") files because
    # CRLF's last byte is "\n". Files with no terminator at all → W292.
    if not content.endswith("\n"):
        return False, (
            f"{path}: file must end with a newline (ruff W292). "
            "Add a single '\\n' at EOF."
        )
    # splitlines() strips both "\n" and "\r\n" terminators, so a CRLF file
    # doesn't get its trailing "\r" misread as W291 trailing whitespace.
    bad_lines: List[int] = []
    for i, line in enumerate(content.splitlines(), start=1):
        if line and line != line.rstrip():
            bad_lines.append(i)
            if len(bad_lines) >= _MAX_REPORTED_ISSUES:
                break
    if bad_lines:
        return False, (
            f"{path}: trailing whitespace on line(s) "
            f"{', '.join(map(str, bad_lines))} (ruff W291). Strip the "
            "trailing spaces."
        )
    return True, ""


def _get_repo_files(repo: Optional[str], branch: str) -> Optional[Set[str]]:
    """Fetch and cache the file list for `branch`.

    Per-(repo, branch) cache with a 1 h TTL — short-lived task branches
    don't accumulate forever. Returns None when the listing can't be
    fetched (GitHub down / branch missing) — callers should treat that
    as "skip the local-import check" rather than block on it.

    Subsequent successful writes call `mark_path_written` to extend the
    cache in place, so a file created at iteration N is "visible" when
    iteration N+1 imports it without paying a fresh API round-trip.
    """
    key = (repo, branch)
    now = time.monotonic()
    with _branch_files_lock:
        cached = _branch_files_cache.get(key)
        if cached is not None and (now - cached[0]) < _BRANCH_FILES_TTL_S:
            return cached[1]
    try:
        from app.integrations import github_api  # local to avoid cycles
    except ImportError:
        return None
    try:
        result = github_api.list_repo_tree(repo=repo, ref=branch)
    except Exception as exc:
        logger.debug("[validator] list_repo_tree raised: %s", exc)
        return None
    if not result.get("ok"):
        logger.debug(
            "[validator] list_repo_tree failed for %s@%s: %s",
            repo, branch, result.get("error"),
        )
        return None
    paths: Set[str] = set(result.get("paths") or [])
    with _branch_files_lock:
        _branch_files_cache[key] = (now, paths)
    return paths


def mark_path_written(repo: Optional[str], branch: str, path: str) -> None:
    """Record that `path` now exists on `branch` so the local-import
    check immediately sees it on the next write.

    No-op if the cache for this branch hasn't been populated yet — the
    next `_get_repo_files` call will fetch the fresh list anyway.
    """
    key = (repo, branch)
    with _branch_files_lock:
        cached = _branch_files_cache.get(key)
        if cached is not None:
            cached[1].add(path)


def _validate_local_imports(
    content: str,
    path: str,
    repo_files: Set[str],
) -> Tuple[bool, str]:
    """Flag `from X.Y import Z` / `import X.Y` for local modules that
    don't exist on the branch.

    A name is considered *local* only when its top-level component matches
    a top-level package in the branch tree (with or without a `cloud/`
    prefix, which is Sandy's layout). Stdlib and third-party imports
    skip silently — their packages aren't in the repo tree.

    Relative imports (`from .foo import bar`) are skipped — resolving
    them would need the importer's package context, and the file is on
    its way to GitHub anyway where the real path is.
    """
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError:
        return True, ""

    issues: List[str] = []
    seen: Set[Tuple[int, str]] = set()

    for node in ast.walk(tree):
        targets: List[Tuple[int, str]] = []
        if isinstance(node, ast.ImportFrom):
            if (node.level or 0) > 0:
                continue
            if node.module:
                targets.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                targets.append((node.lineno, alias.name))

        for lineno, dotted in targets:
            key = (lineno, dotted)
            if key in seen:
                continue
            seen.add(key)
            problem = _resolve_dotted(dotted, repo_files)
            if problem:
                issues.append(f"line {lineno}: {problem}")
                if len(issues) >= _MAX_REPORTED_ISSUES:
                    break
        if len(issues) >= _MAX_REPORTED_ISSUES:
            break

    if issues:
        return False, (
            f"{path}: local import issues:\n  - "
            + "\n  - ".join(issues[:_MAX_REPORTED_ISSUES])
        )
    return True, ""


def _resolve_dotted(dotted: str, repo_files: Set[str]) -> Optional[str]:
    """Decide whether a dotted import is a *missing local module*.

    Returns:
        None    → resolves OR is third-party/stdlib (don't flag).
        str     → message describing the missing local module.
    """
    parts = [p for p in dotted.split(".") if p]
    if not parts:
        return None

    top = parts[0]

    def _is_pkg_root(prefix: str) -> bool:
        if f"{prefix}/__init__.py" in repo_files:
            return True
        return any(p.startswith(f"{prefix}/") for p in repo_files)

    prefixes: List[str] = []
    if _is_pkg_root(top):
        prefixes.append("")
    if _is_pkg_root(f"cloud/{top}"):
        prefixes.append("cloud/")

    if not prefixes:
        return None  # Top-level name isn't a package in this repo

    base = "/".join(parts)
    candidates = [f"{pre}{base}.py" for pre in prefixes]
    candidates += [f"{pre}{base}/__init__.py" for pre in prefixes]
    if any(c in repo_files for c in candidates):
        return None

    return (
        f"local import `{dotted}` doesn't resolve on the branch "
        f"(looked for {candidates[0]} / {candidates[len(prefixes)]}). "
        "Forgot to create the module, or wrong path?"
    )
