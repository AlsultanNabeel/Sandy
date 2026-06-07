"""تكامل Notion — حفظ خطط العصف الذهني كصفحات.

يحتاج متغيّرين بيئة:
  • `NOTION_API_KEY`        — توكن تكامل Notion (secret_...).
  • `NOTION_PARENT_PAGE_ID` — معرّف الصفحة الأم اللي تتحفظ تحتها الخطط (شاركها مع التكامل).

تعطّل آمن: لو التوكن مش مضبوط → `is_configured()` بترجّع False والميزة بتكمل
بدون Notion (تحفظ بـMongo بس).
"""

from __future__ import annotations

import logging
import os
import re
import requests
from typing import List, Optional

logger = logging.getLogger(__name__)

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"
_TIMEOUT = 15


def _api_key() -> str:
    return os.getenv("NOTION_API_KEY", "").strip()


def _parent_page_id() -> str:
    return os.getenv("NOTION_PARENT_PAGE_ID", "").strip()


def is_configured() -> bool:
    return bool(_api_key() and _parent_page_id())


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Notion-Version": _VERSION,
        "Content-Type": "application/json",
    }


def _rich_text(content: str) -> list:
    return [{"type": "text", "text": {"content": content[:1900]}}]


def _markdown_to_blocks(text: str) -> List[dict]:
    """تحويل markdown بسيط → بلوكات Notion (عناوين/نقاط/فقرات). حد 100 بلوك."""
    blocks: List[dict] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()
        if stripped.startswith("### "):
            blocks.append({"object": "block", "type": "heading_3",
                           "heading_3": {"rich_text": _rich_text(stripped[4:])}})
        elif stripped.startswith("## "):
            blocks.append({"object": "block", "type": "heading_2",
                           "heading_2": {"rich_text": _rich_text(stripped[3:])}})
        elif stripped.startswith("# "):
            blocks.append({"object": "block", "type": "heading_1",
                           "heading_1": {"rich_text": _rich_text(stripped[2:])}})
        elif stripped[:6].lower() in ("- [ ] ", "- [x] ") or stripped[:6].lower() == "* [ ] ":
            checked = stripped[3].lower() == "x"
            blocks.append({"object": "block", "type": "to_do",
                           "to_do": {"rich_text": _rich_text(stripped[6:]), "checked": checked}})
        elif stripped == "---":
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        elif stripped[:2] in ("- ", "* ") or stripped.startswith("• "):
            blocks.append({"object": "block", "type": "bulleted_list_item",
                           "bulleted_list_item": {"rich_text": _rich_text(stripped[2:])}})
        elif len(stripped) > 2 and stripped[0].isdigit() and stripped[1] in ".)":
            blocks.append({"object": "block", "type": "numbered_list_item",
                           "numbered_list_item": {"rich_text": _rich_text(stripped[2:].lstrip())}})
        else:
            blocks.append({"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": _rich_text(stripped)}})
        if len(blocks) >= 100:
            break
    return blocks


def create_plan_page(title: str, markdown_text: str) -> Optional[str]:
    """ينشئ صفحة Notion بالعنوان + المحتوى، يرجّع رابطها أو None لو فشل."""
    if not is_configured():
        return None
    try:
        payload = {
            "parent": {"page_id": _parent_page_id()},
            "properties": {
                "title": {"title": _rich_text(title or "خطة")},
            },
            "children": _markdown_to_blocks(markdown_text),
        }
        resp = requests.post(
            f"{_API}/pages", headers=_headers(), json=payload, timeout=_TIMEOUT
        )
        if resp.status_code >= 300:
            logger.warning("[notion] create page failed %s: %s", resp.status_code, resp.text[:300])
            return None
        return resp.json().get("url")
    except Exception as e:  # noqa: BLE001
        logger.warning("[notion] create page error: %s", e)
        return None


def archive_page(page_url_or_id: str) -> bool:
    """يأرشف صفحة (يبعتها لسلّة Notion). يرجّع True لو نجح."""
    if not is_configured():
        return False
    pid = _extract_page_id(page_url_or_id)
    if not pid:
        return False
    try:
        resp = requests.patch(
            f"{_API}/pages/{pid}", headers=_headers(),
            json={"archived": True}, timeout=_TIMEOUT,
        )
        return resp.status_code < 300
    except Exception as e:  # noqa: BLE001
        logger.warning("[notion] archive page error: %s", e)
        return False


def _extract_page_id(url_or_id: str) -> Optional[str]:
    """يطلّع معرّف صفحة Notion (آخر 32 خانة hex) من رابط أو معرّف."""
    if not url_or_id:
        return None
    # شيل أي شي بعد ?، وكل حرف مش hex (مسافات/شرطات/سلاش/اسم الصفحة) → ياخد آخر 32
    hexes = re.sub(r"[^0-9a-fA-F]", "", url_or_id.split("?")[0])
    return hexes[-32:] if len(hexes) >= 32 else None


def update_page_content(page_url_or_id: str, markdown_text: str) -> bool:
    """يعيد كتابة محتوى صفحة موجودة: يأرشف بلوكاتها الحالية ويكتب المحتوى الجديد.

    يرجّع True لو نجح. بنحافظ على نفس الصفحة (تعديل فعلي مش صفحة جديدة).
    """
    if not is_configured():
        return False
    pid = _extract_page_id(page_url_or_id)
    if not pid:
        return False
    try:
        # 1) اقرأ البلوكات الحالية وأرشفها (حذف ناعم)
        r = requests.get(
            f"{_API}/blocks/{pid}/children?page_size=100",
            headers=_headers(), timeout=_TIMEOUT,
        )
        if r.status_code >= 300:
            logger.warning("[notion] list children failed %s: %s", r.status_code, r.text[:200])
            return False
        for blk in r.json().get("results", []):
            bid = blk.get("id")
            if not bid:
                continue
            try:
                requests.patch(
                    f"{_API}/blocks/{bid}", headers=_headers(),
                    json={"archived": True}, timeout=_TIMEOUT,
                )
            except Exception:  # noqa: BLE001
                pass
        # 2) اكتب المحتوى الجديد
        ar = requests.patch(
            f"{_API}/blocks/{pid}/children", headers=_headers(),
            json={"children": _markdown_to_blocks(markdown_text)}, timeout=_TIMEOUT,
        )
        if ar.status_code >= 300:
            logger.warning("[notion] append failed %s: %s", ar.status_code, ar.text[:200])
            return False
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[notion] update page error: %s", e)
        return False
