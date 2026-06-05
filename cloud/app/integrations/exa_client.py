"""Exa search client with circuit breaker and async wrappers."""

import asyncio
import logging
from typing import Any, Dict, List

import requests

from app.utils.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

_cb = CircuitBreaker(name="exa", failure_threshold=5, recovery_timeout=60.0)


def _do_search(
    query: str, exa_api_key: str, num_results: int, timeout: int
) -> List[Dict[str, Any]]:
    url = "https://api.exa.ai/search"
    headers = {"x-api-key": exa_api_key, "Content-Type": "application/json"}
    payload = {
        "query": query,
        "numResults": num_results,
        "type": "auto",
        "contents": {"text": True},
    }
    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    results = []
    for item in data.get("results", []):
        results.append(
            {
                "title": str(item.get("title") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "text": str(item.get("text") or "").strip(),
                "published_date": str(item.get("publishedDate") or "").strip(),
            }
        )
    return results


def _do_get_contents(url: str, exa_api_key: str, timeout: int) -> Dict[str, Any]:
    api_url = "https://api.exa.ai/contents"
    headers = {"x-api-key": exa_api_key, "Content-Type": "application/json"}
    payload = {"urls": [url], "text": True}
    response = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    results = data.get("results", [])
    if not results:
        return {}
    item = results[0]
    return {
        "url": str(item.get("url") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "text": str(item.get("text") or "").strip(),
    }


def search_exa(
    query: str,
    exa_api_key: str,
    num_results: int = 10,
    timeout: int = 60,
) -> List[Dict[str, Any]]:
    """Search Exa and return simplified results. Protected by circuit breaker."""
    if not exa_api_key:
        logger.warning("[Exa] EXA_API_KEY missing")
        return []
    try:
        results = _cb.call(_do_search, query, exa_api_key, num_results, timeout)
        logger.info("[Exa] found %d results for: %s", len(results), query)
        return results
    except CircuitOpenError:
        logger.warning("[Exa] circuit open, skipping search")
        return []
    except Exception as e:
        logger.error("[Exa] search failed: %s", e)
        return []


def get_exa_page_content(
    url: str,
    exa_api_key: str,
    timeout: int = 60,
) -> Dict[str, Any]:
    """Fetch page contents from Exa. Protected by circuit breaker."""
    if not exa_api_key:
        logger.warning("[Exa] EXA_API_KEY missing")
        return {}
    try:
        return _cb.call(_do_get_contents, url, exa_api_key, timeout)
    except CircuitOpenError:
        logger.warning("[Exa] circuit open, skipping content fetch")
        return {}
    except Exception as e:
        logger.error("[Exa] contents fetch failed for %s: %s", url, e)
        return {}


async def search_exa_async(
    query: str,
    exa_api_key: str,
    num_results: int = 10,
    timeout: int = 60,
) -> List[Dict[str, Any]]:
    """Async wrapper for search_exa. Runs in a thread pool so it won't block the loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: search_exa(query, exa_api_key, num_results, timeout)
    )


async def get_exa_page_content_async(
    url: str,
    exa_api_key: str,
    timeout: int = 60,
) -> Dict[str, Any]:
    """Async wrapper for get_exa_page_content. Runs in a thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: get_exa_page_content(url, exa_api_key, timeout)
    )
