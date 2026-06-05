"""Research data pipeline — URL normalization, AI extraction, dedup, filter, rank.

Public API:
  run_research_pipeline(...)  -> List[dict]
  deduplicate_research_results(results) -> List[dict]
  filter_research_results(results, preference) -> List[dict]
  rank_research_results(results, preference) -> List[dict]
  is_official_source_url(url, research_type) -> bool
"""

import json
import re
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

# URL and name normalisation


def normalize_result_url(url: str) -> str:
    url = str(url or "").strip().lower().rstrip("/")
    for suffix in ("/en", "/es", "/ar", "/fr", "/de", "/ca", "/eu"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url.split("?")[0]


def normalize_program_name(name: str) -> str:
    name = str(name or "").strip().lower()
    replacements = {
        "robótica": "robotics",
        "robotica": "robotics",
        "automatización": "automation",
        "automatizacion": "automation",
        "automática": "automation",
        "automatica": "automation",
        "máster": "master",
        "master's": "master",
        "masters": "master",
    }
    for old, new in replacements.items():
        name = name.replace(old, new)
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


# URL quality check


def is_official_source_url(url: str, research_type: str = "general") -> bool:
    if not url:
        return False
    lowered = url.lower()

    blocked = [
        "educations.com",
        "educations.es",
        "mastersportal.com",
        "masterstudies.com",
        "findamasters.com",
        "studyportals.com",
        "bachelorstudies.com",
        "phdstudies.com",
        "financialmagazine.es",
        "universoptimum.com",
        "yaq.es",
        "linkedin.com",
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "medium.com",
        "reddit.com",
        "quora.com",
        "wikipedia.org",
        "topuniversities.com",
        "timeshighereducation.com",
        "shiksha.com",
        "ielts.org",
        "ielts.idp.com",
    ]
    if any(d in lowered for d in blocked):
        return False

    if research_type == "education":
        official_hints = [
            ".edu",
            ".ac.",
            ".ac.uk",
            "universidad",
            "university",
            "/master",
            "/masters",
            "/graduate",
            "/admissions",
            "/program",
            "/postgrado",
            "/estudio",
            "/degree",
            "/degrees",
        ]
        if any(h in lowered for h in official_hints):
            return True
        if lowered.startswith("https://www.") or lowered.startswith("https://"):
            if ".es/" in lowered or lowered.endswith(".es") or ".edu/" in lowered:
                return True
        return False

    if research_type == "travel":
        return any(
            h in lowered
            for h in [
                ".gov",
                ".gob",
                ".eu",
                "official",
                "visit",
                "tourism",
                "booking.com",
                "airbnb.com",
                "expedia.com",
            ]
        )
    if research_type == "product":
        return any(
            h in lowered
            for h in [
                "amazon.",
                "mediamarkt.",
                "coolblue.",
                "bol.",
                "apple.",
                "sony.",
                "dell.",
                "ikea.",
            ]
        )
    if research_type == "news":
        return any(h in lowered for h in [".com", ".org", ".net"])
    return True


# AI-based page extraction


def extract_structured_page_data(
    page_content: Dict[str, Any],
    research_type: str = "general",
    create_chat_completion_fn: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    if not page_content:
        return {}

    page_text = (page_content.get("text", "") or "").strip()
    page_title = page_content.get("title", "")
    page_url = page_content.get("url", "")

    effective_type = research_type
    education_hints = [
        "master",
        "masters",
        "máster",
        "universitario",
        "universidad",
        "university",
        "robotics",
        "robótica",
        "robotica",
        "automática",
        "automatica",
        "admission",
        "credits",
        "ects",
        "preinscripción",
        "preinscripcion",
    ]
    combined = f"{page_title}\n{page_url}\n{page_text[:2000]}".lower()
    if research_type == "general" and any(h in combined for h in education_hints):
        effective_type = "education"

    if not page_text:
        return {}

    prompts = {
        "education": (
            "Extract: institution_name, program_name, degree_level, country, city, "
            "language_of_instruction, admission_requirements, english_requirement, "
            "requires_ielts_or_toefl (true/false/unknown), tuition, deadline, "
            "application_url, official_program_url, summary. Return valid JSON only."
        ),
        "travel": (
            "Extract: place_name, country, city, type, price, booking_link, visa_info, "
            "important_requirements, summary. Return valid JSON only."
        ),
        "product": (
            "Extract: product_name, brand, price, currency, availability, key_features, "
            "pros, cons, official_url, summary. Return valid JSON only."
        ),
        "news": (
            "Extract: headline, publisher, published_date, key_points, summary, source_url. "
            "Return valid JSON only."
        ),
        "general": (
            "Extract: title, main_entity, rating (number/empty), price_or_cost, address, "
            "phone, official_link, summary. Return valid JSON only."
        ),
    }
    instruction = prompts.get(effective_type, prompts["general"])

    if create_chat_completion_fn is None:
        print("[Research] create_chat_completion_fn missing")
        return {
            "title": page_title,
            "official_link": page_url,
            "summary": (page_text[:700] + "...") if len(page_text) > 700 else page_text,
        }

    try:
        response = create_chat_completion_fn(
            messages=[
                {"role": "system", "content": instruction},
                {
                    "role": "user",
                    "content": f"PAGE TITLE: {page_title}\nPAGE URL: {page_url}\n\nPAGE TEXT:\n{page_text[:12000]}",
                },
            ],
            temperature=0,
            max_tokens=900,
            response_format={"type": "json_object"},
            prefer_azure=True,
        )
        return json.loads(response.choices[0].message.content or "{}")
    except Exception as e:
        print(f"[Research] structured extraction failed for {page_url}: {e}")
        return {
            "title": page_title,
            "official_link": page_url,
            "summary": (page_text[:700] + "...") if len(page_text) > 700 else page_text,
        }


def normalize_education_page_data(
    page_data: Dict[str, Any], source_url: str = ""
) -> Dict[str, Any]:
    if not isinstance(page_data, dict):
        return {}

    cleaned = dict(page_data)

    def cv(key: str) -> str:
        return str(cleaned.get(key) or "").strip()

    unknown = {
        "unknown",
        "not specified",
        "not specified.",
        "n/a",
        "none",
        "no especificado",
        "no especificado en la página.",
        "no especificado en la pagina.",
        "desconocido",
    }

    lang = cv("language_of_instruction")
    ielts = cv("requires_ielts_or_toefl")
    tuition = cv("tuition")
    deadline = cv("deadline")

    if lang.lower() in unknown:
        cleaned["language_of_instruction"] = ""
    if ielts.lower() in unknown:
        cleaned["requires_ielts_or_toefl"] = ""
    if tuition.lower() in unknown:
        cleaned["tuition"] = ""
    if deadline.lower() in unknown:
        cleaned["deadline"] = ""

    src_lower = source_url.lower()
    inst_lower = cv("institution_name").lower()

    if (
        "valencia" in inst_lower
        or "universitat politècnica de valència" in inst_lower
        or "upv.es" in src_lower
    ):
        if cleaned.get("country", "").lower() not in {"spain", "españa", "espana", ""}:
            cleaned["country"] = "Spain"
        if cleaned.get("city", "").lower() not in {"valencia", ""}:
            cleaned["city"] = "Valencia"

    if "universidad de alicante" in inst_lower or "ua.es" in src_lower:
        if cleaned.get("country", "").lower() not in {"spain", "españa", "espana", ""}:
            cleaned["country"] = "Spain"
        if cleaned.get("city", "").lower() not in {"alicante", ""}:
            cleaned["city"] = "Alicante"

    if "carlos iii" in inst_lower or "uc3m.es" in src_lower:
        if cleaned.get("country", "").lower() not in {"spain", "españa", "espana", ""}:
            cleaned["country"] = "Spain"

    return cleaned


# Deduplication


def build_result_dedup_key(item: Dict[str, Any]) -> str:
    page_data = item.get("page_data", {}) or {}
    institution = str(page_data.get("institution_name") or "").strip().lower()
    program = normalize_program_name(
        page_data.get("program_name") or item.get("source_title") or ""
    )
    source_url = normalize_result_url(item.get("source_url") or "")
    source_domain = source_path = ""
    try:
        parsed = urlparse(source_url)
        source_domain = parsed.netloc.replace("www.", "").strip().lower()
        source_path = parsed.path.strip().lower()
    except Exception:
        pass

    if institution and program:
        return f"{institution}::{program}"
    if source_domain and program:
        return f"{source_domain}::{program}"
    if source_domain and source_path:
        return f"{source_domain}::{source_path}"
    if source_url:
        return source_url
    return normalize_program_name(item.get("source_title") or "")


def deduplicate_research_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: List[Dict[str, Any]] = []
    seen: set = set()
    for item in results:
        key = build_result_dedup_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


# Pipeline


def run_research_pipeline(
    user_query: str,
    research_type: str = "general",
    requested_count: int = 5,
    search_exa_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    get_exa_page_content_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    create_chat_completion_fn: Optional[Callable[..., Any]] = None,
    exa_api_key: str = "",
    web_research_max_candidates: int = 30,
) -> List[Dict[str, Any]]:
    print(f"[Research] starting {research_type} research for: {user_query}")

    if search_exa_fn is None or get_exa_page_content_fn is None:
        print("[Research] missing Exa dependencies")
        return []

    exa_results = search_exa_fn(
        user_query, exa_api_key=exa_api_key, num_results=web_research_max_candidates
    )
    if not exa_results:
        return []

    candidates = [
        r
        for r in exa_results
        if r.get("url")
        and is_official_source_url(r["url"], research_type=research_type)
    ]

    if not candidates:
        print("[Research] no official-looking candidates found after filtering")
        soft_blocked = [
            "educations.com",
            "educations.es",
            "mastersportal.com",
            "masterstudies.com",
            "findamasters.com",
            "studyportals.com",
            "financialmagazine.es",
            "universoptimum.com",
            "yaq.es",
            "edurank.org",
            "erudera.com",
            "topuniversities.com",
            "timeshighereducation.com",
            "shiksha.com",
        ]
        candidates = [
            r
            for r in exa_results
            if r.get("url") and not any(b in r["url"].lower() for b in soft_blocked)
        ][: max(requested_count * 2, requested_count)]

    extracted: List[Dict[str, Any]] = []
    for item in candidates[: max(requested_count * 2, requested_count)]:
        url = item.get("url", "")
        page_content = get_exa_page_content_fn(url, exa_api_key=exa_api_key)
        page_data = extract_structured_page_data(
            page_content,
            research_type=research_type,
            create_chat_completion_fn=create_chat_completion_fn,
        )
        if research_type == "education":
            page_data = normalize_education_page_data(page_data, source_url=url)
        print(
            f"[Research] Parsed keys for {url}: {list(page_data.keys()) if isinstance(page_data, dict) else 'N/A'}"
        )
        extracted.append(
            {
                "source_title": item.get("title", ""),
                "source_url": url,
                "exa_snippet": item.get("text", ""),
                "page_content": page_content,
                "page_data": page_data,
            }
        )

    deduped = deduplicate_research_results(extracted)
    print(f"[Research] {len(extracted)} extracted, {len(deduped)} unique")
    return deduped
