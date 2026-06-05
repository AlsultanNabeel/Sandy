"""Research result formatting — converts structured research data to Arabic display strings.

Public API:
  summarize_research_results(results, requested_count) -> str
"""

import json
from typing import Any, Dict, List


def _localize_value(value: Any, field_type: str = "") -> str:
    if isinstance(value, dict):
        if field_type == "deadline":
            parts = [
                f"{str(k).replace('_', ' ')}: {str(v)}"
                for k, v in value.items()
                if str(v).strip()
            ]
            return " | ".join(parts) if parts else "غير مذكور بوضوح"
        value = str(value)
    if isinstance(value, list):
        value = " | ".join(str(x).strip() for x in value if str(x).strip())

    value = str(value or "").strip()
    lowered = value.lower()

    if field_type == "deadline" and value.startswith("{") and value.endswith("}"):
        try:
            parsed = json.loads(value.replace("'", '"'))
            if isinstance(parsed, dict):
                parts = [
                    f"{str(k).replace('_', ' ')}: {str(v)}"
                    for k, v in parsed.items()
                    if str(v).strip()
                ]
                return " | ".join(parts) if parts else "غير مذكور بوضوح"
        except Exception:
            pass

    unknown_values = {
        "unknown",
        "not specified",
        "not specified.",
        "not specified in the provided text",
        "not specified in the provided text.",
        "n/a",
        "none",
        "no especificado",
        "no especificado en la página",
        "no especificado en la página.",
        "no especificado en la pagina",
        "no especificado en la pagina.",
        "desconocido",
    }
    if lowered in unknown_values:
        if field_type == "tuition":
            return "غير مذكورة بوضوح"
        if field_type == "deadline":
            return "غير مذكور بوضوح"
        return "غير واضح"

    if lowered in {"yes", "true", "required"}:
        return "نعم"
    if lowered in {"no", "false", "not required"}:
        return "لا"
    return value


def _result_title(page_data: Dict[str, Any], fallback: str) -> str:
    return (
        page_data.get("program_name")
        or page_data.get("product_name")
        or page_data.get("place_name")
        or page_data.get("headline")
        or page_data.get("title")
        or fallback
        or "بدون عنوان"
    )


def _source_label(item: Dict[str, Any], page_data: Dict[str, Any]) -> str:
    institution = str(page_data.get("institution_name") or "").strip()
    publisher = str(page_data.get("publisher") or "").strip()
    url = str(
        item.get("source_url")
        or page_data.get("official_program_url")
        or page_data.get("application_url")
        or ""
    ).strip()

    if institution:
        return institution
    if publisher:
        return publisher
    if url:
        return url
    return "غير مذكور"


def summarize_research_results(
    results: List[Dict[str, Any]], requested_count: int = 5
) -> str:
    if not results:
        return "[think] ما لقيت نتائج واضحة من البحث حالياً."

    lines = []
    for i, item in enumerate(results[:requested_count], 1):
        pd = item.get("page_data", {}) or {}
        title = str(_result_title(pd, item.get("source_title", ""))).strip()
        summary = str(pd.get("summary") or "").strip()
        source = _source_label(item, pd)
        url = str(
            item.get("source_url")
            or pd.get("official_program_url")
            or pd.get("application_url")
            or ""
        ).strip()

        degree_level = str(pd.get("degree_level") or "").strip()
        country = str(pd.get("country") or "").strip()
        city = str(pd.get("city") or "").strip()
        lang = _localize_value(pd.get("language_of_instruction"), "language")
        ielts = _localize_value(pd.get("requires_ielts_or_toefl"), "ielts")
        tuition = _localize_value(pd.get("tuition"), "tuition")
        deadline = _localize_value(pd.get("deadline"), "deadline")
        app_url = _localize_value(pd.get("application_url"), "url")
        prog_url = _localize_value(pd.get("official_program_url"), "url")

        lines.append(f"{i}. {title}")
        lines.append(f"المصدر: {source}")

        meta = []
        if degree_level:
            meta.append(f"الدرجة: {degree_level}")
        if country:
            meta.append(f"الدولة: {country}")
        if city:
            meta.append(f"المدينة: {city}")
        if meta:
            lines.append("المعلومات: " + " | ".join(meta))

        if summary:
            lines.append(f"الملخص: {summary}")

        points = []
        if lang and lang not in {"غير واضح", "غير واضحة"}:
            points.append(f"لغة الدراسة: {lang}")
        if ielts and ielts != "غير واضح":
            points.append(f"IELTS/TOEFL: {ielts}")
        if tuition and tuition != "غير مذكورة بوضوح":
            points.append(f"الرسوم: {tuition}")
        if deadline and deadline != "غير مذكور بوضوح":
            points.append(f"الموعد النهائي: {deadline}")
        if points:
            lines.append("نقاط مهمة:")
            lines.extend(f"- {p}" for p in points)

        if prog_url:
            lines.append(f"رابط البرنامج: {prog_url}")
        if app_url:
            lines.append(f"رابط التقديم: {app_url}")
        elif not prog_url and url:
            lines.append(f"الرابط: {url}")

        lines.append("")

    return "\n".join(lines)
