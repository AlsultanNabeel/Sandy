"""
Deep contextual memory: recent search/options buffer + last-action pointer for Planner/chat.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.utils.time import USER_TZ

LAST_SEARCH_RESULTS_KEY = "last_search_results"
LAST_ACTION_CONTEXT_KEY = "last_action_context"

_COMPARISON_HINT = re.compile(
    r"(?:أحسن|احسن|أفضل|افضل|ارخص|أرخص|أغلا|اغلا|أقرب|اقرب|أبعد|ابعد|أسرع|اسرع|"
    r"اي\s+وحد|اي\s+واحد|أي\s+وحد|أي\s+واحد|أي\s+من(?:هم|هن)|اي\s+من|ومين|وش\s+المفضل|شو\s+المفضل|"
    r"(?:مش|مو)\s+عرف\s+اختار)",
    re.IGNORECASE,
)


def persist_last_search_results(
    session: Dict[str, Any],
    *,
    domain: str,
    query: str,
    items: List[Dict[str, Any]],
) -> None:
    """Store last surfaced options for comparative / grounding follow-ups."""
    sess = session if isinstance(session, dict) else {}
    clean_items: List[Dict[str, Any]] = []
    for it in items[:20]:
        if not isinstance(it, dict):
            continue
        row = it.get("row")
        title = str(it.get("title", "") or "").strip()[:500]
        url = str(it.get("url", "") or "").strip()[:500]
        snippet = str(it.get("snippet", "") or "").strip()[:900]
        if not title and url:
            title = url
        if title or snippet or url:
            clean_items.append(
                {
                    "row": row if isinstance(row, int) else len(clean_items) + 1,
                    "title": title or "بدون عنوان",
                    "url": url,
                    "snippet": snippet,
                }
            )
    sess[LAST_SEARCH_RESULTS_KEY] = {
        "domain": str(domain or "unknown").strip()[:80],
        "query": str(query or "").strip()[:300],
        "items": clean_items,
        "ts": datetime.now(USER_TZ).isoformat(),
    }


def record_last_action(
    session: Dict[str, Any],
    kind: str,
    *,
    summary: str,
    refs: Optional[Dict[str, Any]] = None,
) -> None:
    """Pointer for elliptical follow-ups (احذفه، عدلي…)."""
    sess = session if isinstance(session, dict) else {}
    sess[LAST_ACTION_CONTEXT_KEY] = {
        "kind": str(kind or "unknown").strip()[:64],
        "summary": str(summary or "").strip()[:400],
        "refs": dict(refs) if isinstance(refs, dict) else {},
        "ts": datetime.now(USER_TZ).isoformat(),
    }


def places_to_search_items(
    places: List[Dict[str, Any]], limit: int = 12
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, p in enumerate((places or [])[:limit]):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", "") or "").strip()
        if not name:
            continue
        rating = p.get("rating") or ""
        addr = str(p.get("address", "") or "").strip()
        price = str(p.get("price_level", "") or "").strip()
        snip_parts = [x for x in (f"⭐{rating}", price, addr) if x]
        out.append(
            {
                "row": i + 1,
                "title": name,
                "url": str(p.get("maps_url", "") or "").strip(),
                "snippet": " — ".join(snip_parts)[:500],
            }
        )
    return out



def build_agent_runtime_state(session: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Compact JSON-safe blob for Planner user payload."""
    sess = session if isinstance(session, dict) else {}
    lac = sess.get(LAST_ACTION_CONTEXT_KEY)
    if not isinstance(lac, dict):
        lac = {}

    sr = sess.get(LAST_SEARCH_RESULTS_KEY)
    if not isinstance(sr, dict):
        sr = {}

    items = sr.get("items") if isinstance(sr.get("items"), list) else []
    preview_rows: List[Dict[str, Any]] = []
    for it in items[:8]:
        if not isinstance(it, dict):
            continue
        preview_rows.append(
            {
                "row": it.get("row"),
                "title": str(it.get("title", "") or "")[:220],
                "url": str(it.get("url", "") or "")[:240],
                "snippet": str(it.get("snippet", "") or "")[:260],
            }
        )

    draft = sess.get("email_send_draft")
    draft_waiting = ""
    if isinstance(draft, dict):
        draft_waiting = str(draft.get("waiting", "") or "").strip()

    return {
        "last_action": {
            "kind": lac.get("kind"),
            "summary": str(lac.get("summary", "") or "")[:300],
            "refs_keys": (
                sorted(str(k) for k in (lac.get("refs") or {}).keys())
                if isinstance(lac.get("refs"), dict)
                else []
            ),
        },
        "last_search": {
            "domain": sr.get("domain"),
            "query": str(sr.get("query", "") or "")[:200],
            "n_items": len(items),
            "items_preview": preview_rows,
            "has_buffer": len(preview_rows) > 0,
        },
        "email_send_draft_waiting": draft_waiting or None,
    }


def runtime_state_chat_block(session: Optional[Dict[str, Any]]) -> str:
    """Short Arabic block for Sandy chat system prompt."""
    blob = build_agent_runtime_state(session)
    parts: List[str] = []
    la = blob.get("last_action") or {}
    if la.get("kind"):
        parts.append(f"آخر نشاط تشغيلي: {la.get('kind')} — {la.get('summary', '')}")

    sr = blob.get("last_search") or {}
    if sr.get("has_buffer"):
        n = sr.get("n_items", 0)
        dq = sr.get("query", "")
        parts.append(
            f"آخر خيارات/نتائج مُعرضة للمستخدم ({sr.get('domain')}, عن «{dq}»، عددها ~{n})."
        )

    dw = blob.get("email_send_draft_waiting")
    if dw:
        draft = session.get("email_send_draft") if isinstance(session, dict) else {}
        to_txt = ""
        if isinstance(draft, dict):
            to_txt = str(draft.get("to", "") or "").strip()
        parts.append(
            f"في انتظار نص رسالة إلى {to_txt or '(مجهول)'}."
            if to_txt
            else "في انتظار نص الإرسال."
        )

    if not parts:
        return ""
    return (
        "🧭 الحالة الراهنة للوكيل (استخدمها لحل الإشارات الضمنية؛ لا تعيد البحث إلا إذا طلب المستخدم جلب جديد):\n- "
        + "\n- ".join(parts)
    )


def wants_comparison_grounded_in_search(normalized_message: str) -> bool:
    """Heuristic signal for Planner prompt (comparison without explicit noun)."""
    return bool(_COMPARISON_HINT.search(str(normalized_message or "")))
