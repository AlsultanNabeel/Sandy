"""Semantic memory on top of MongoDB + OpenAI embeddings.

Collections:
  sandy_facts             user facts from the learning system
  sandy_conversations     recent (user, assistant) turns
  sandy_context_metadata  per-turn topic tracking

Every doc has a chat_id. Reads and writes are scoped to the current user's
chat_id (from the active thread-local profile). Owner and family read and
write their own memory; guests and unauthenticated get nothing.

Legacy docs with no chat_id get tagged with OWNER_CHAT_ID on first startup
so the owner keeps access to them.

Search tries, in order:
  1. Atlas $vectorSearch (semantic, needs the vector index)
  2. $text search (keyword fallback)
  3. sort by usage_count / ts (last resort if there's no text index either)

Call init_mongo_memory(mongo_db, openai_client) once at startup.
"""

import hashlib
import logging
from typing import Any, Dict, List, Optional

from app.utils.user_profiles import get_active_user_profile, OWNER_CHAT_ID

logger = logging.getLogger(__name__)

# Warn only once per process when vector search degrades to keyword sort.
_vector_search_warned = False

_mongo_db = None
_openai_client = None

_EMBEDDING_MODEL = "text-embedding-3-small"
_EMBEDDING_DIMS = 1536
_VECTOR_INDEX = "sandy_vector_index"


# Profile helpers


def _current_chat_id() -> Optional[str]:
    """Return the active user's chat_id, or None if no profile is set."""
    profile = get_active_user_profile()
    if not profile:
        return None
    cid = str(profile.get("chat_id", "") or "").strip()
    return cid or None


def _can_write_memory() -> bool:
    """Owner and Family may write. Guest and unauthenticated may not."""
    profile = get_active_user_profile()
    if not profile:
        return False
    relation = str(profile.get("relation", "guest") or "guest").strip().lower()
    return relation in {"owner", "family"}


def _can_read_memory() -> bool:
    """Same rule as write: only owner and family see their memory."""
    return _can_write_memory()


def _user_filter(chat_id: str) -> Dict:
    return {"chat_id": chat_id}


# Init


def init_mongo_memory(mongo_db, openai_client=None) -> None:
    """Store the MongoDB and OpenAI handles, build indexes, migrate legacy docs."""
    global _mongo_db, _openai_client
    _mongo_db = mongo_db
    _openai_client = openai_client

    if mongo_db is None:
        print("[Memory] no MongoDB, memory storage disabled", flush=True)
        return

    try:
        mongo_db["sandy_facts"].create_index(
            [("chat_id", 1), ("text", "text")],
            default_language="none",
            background=True,
        )
        mongo_db["sandy_conversations"].create_index(
            [("chat_id", 1), ("text", "text")],
            default_language="none",
            background=True,
        )
        mongo_db["sandy_facts"].create_index([("chat_id", 1)], background=True)
        mongo_db["sandy_conversations"].create_index([("chat_id", 1)], background=True)
        mongo_db["sandy_context_metadata"].create_index(
            [("timestamp", -1)],
            background=True,
        )
    except Exception as e:
        print(f"[Memory] index setup: {e}", flush=True)

    # tag legacy docs (no chat_id) as the owner's
    if OWNER_CHAT_ID:
        _migrate_legacy_docs(mongo_db)

    mode = "vector + keyword" if openai_client else "keyword only"
    print(f"[Memory] MongoDB memory ready ({mode})", flush=True)


def _migrate_legacy_docs(mongo_db) -> None:
    """Tag docs that predate per-user isolation with chat_id=OWNER_CHAT_ID."""
    for col_name in ("sandy_facts", "sandy_conversations"):
        try:
            result = mongo_db[col_name].update_many(
                {"chat_id": {"$exists": False}},
                {"$set": {"chat_id": OWNER_CHAT_ID}},
            )
            if result.modified_count:
                print(
                    f"[Memory] migrated {result.modified_count} legacy docs "
                    f"in {col_name} to chat_id={OWNER_CHAT_ID}",
                    flush=True,
                )
        except Exception as e:
            print(f"[Memory] migration failed for {col_name}: {e}", flush=True)


# Embeddings


def _importance_score(usage_count: int, created_at=None) -> float:
    """Usage count weighted by recency, decaying to near zero over a year."""
    from datetime import datetime, timezone
    recency = 1.0
    if created_at is not None:
        now = datetime.now(timezone.utc)
        if getattr(created_at, "tzinfo", None) is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_days = max(0, (now - created_at).days)
        recency = max(0.1, 1.0 - age_days / 365.0)
    return (usage_count + 1) * recency


def _embed(text: str) -> Optional[List[float]]:
    """Embed text, or None if there's no client or the call fails."""
    if _openai_client is None or not text:
        return None
    try:
        resp = _openai_client.embeddings.create(
            model=_EMBEDDING_MODEL,
            input=text,
        )
        return resp.data[0].embedding
    except Exception as e:
        print(f"[Memory] embedding failed: {e}", flush=True)
        return None


# ID helpers


def _fact_id(text: str, chat_id: str = "") -> str:
    """ID scoped to a user, so the same text under two users gets two IDs."""
    return (
        "f_"
        + hashlib.sha1(f"{chat_id}:{text}".encode(), usedforsecurity=False).hexdigest()[
            :20
        ]
    )


def _conv_id(user_text: str, assistant_text: str, chat_id: str = "") -> str:
    combined = f"{chat_id}:{user_text}||{assistant_text}"
    return (
        "c_" + hashlib.sha1(combined.encode(), usedforsecurity=False).hexdigest()[:20]
    )


# Facts


def load_facts_to_chroma(facts: List[Dict[str, Any]]) -> None:
    """Upsert user facts, embedding the new ones."""
    if not _can_write_memory():
        return
    if _mongo_db is None or not facts:
        return
    chat_id = _current_chat_id()
    if not chat_id:
        return
    col = _mongo_db["sandy_facts"]
    inserted = 0
    for fact in facts:
        text = (fact.get("text") or "").strip()
        if not text:
            continue
        fid = _fact_id(text, chat_id)
        try:
            if col.count_documents({"_id": fid}) > 0:
                continue
            doc = {
                "_id": fid,
                "chat_id": chat_id,
                "text": text,
                "type": fact.get("type", "general"),
                "usage_count": 0,
                "importance_score": 1.0,
            }
            vec = _embed(text)
            if vec:
                doc["embedding"] = vec
            result = col.update_one(
                {"_id": fid},
                {"$setOnInsert": doc},
                upsert=True,
            )
            if result.upserted_id is not None:
                inserted += 1
        except Exception as e:
            print(f"[Memory] load_facts: {e}", flush=True)
    if inserted:
        print(
            f"[Memory] indexed {inserted} new facts (chat_id={chat_id})", flush=True
        )


# Conversations


def load_conversations_to_chroma(
    conversations: List[Dict[str, Any]], max_recent: int = 60
) -> None:
    """Upsert recent conversation turns, embedding the new ones."""
    if not _can_write_memory():
        return
    if _mongo_db is None or not conversations:
        return
    chat_id = _current_chat_id()
    if not chat_id:
        return
    col = _mongo_db["sandy_conversations"]
    recent = conversations[-max_recent:]
    inserted = 0
    for conv in recent:
        user_text = (conv.get("user") or conv.get("content") or "").strip()
        asst_text = (conv.get("sandy") or conv.get("assistant") or "").strip()
        if not user_text:
            continue
        combined = f"المستخدم: {user_text}"
        if asst_text:
            combined += f"\nساندي: {asst_text}"
        cid = _conv_id(user_text, asst_text, chat_id)
        try:
            if col.count_documents({"_id": cid}) > 0:
                continue
            doc = {
                "_id": cid,
                "chat_id": chat_id,
                "text": combined,
                "role": "conversation",
                "ts": conv.get("timestamp", ""),
            }
            vec = _embed(combined)
            if vec:
                doc["embedding"] = vec
            result = col.update_one(
                {"_id": cid},
                {"$setOnInsert": doc},
                upsert=True,
            )
            if result.upserted_id is not None:
                inserted += 1
        except Exception as e:
            print(f"[Memory] load_conversations: {e}", flush=True)
    if inserted:
        print(
            f"[Memory] indexed {inserted} new conversation turns (chat_id={chat_id})",
            flush=True,
        )


# Search


def _vector_search(
    col, query: str, chat_id: str, n_results: int, extra_project: Dict
) -> Optional[List[Dict]]:
    """$vectorSearch filtered by chat_id, or None if it can't run."""
    vec = _embed(query)
    if not vec:
        return None
    try:
        pipeline = [
            {
                "$vectorSearch": {
                    "index": _VECTOR_INDEX,
                    "path": "embedding",
                    "queryVector": vec,
                    "numCandidates": n_results * 10,
                    "limit": n_results,
                    "filter": {"chat_id": {"$eq": chat_id}},
                }
            },
            {
                "$project": {
                    "text": 1,
                    "score": {"$meta": "vectorSearchScore"},
                    **extra_project,
                }
            },
        ]
        return list(col.aggregate(pipeline))
    except Exception as exc:
        global _vector_search_warned
        if not _vector_search_warned:
            logger.warning(
                "[chroma] vector search failed, falling back to keyword sort: %s", exc
            )
            _vector_search_warned = True
        return None


def search_relevant_facts(query: str, n_results: int = 5) -> List[str]:
    """Semantic search over the current user's facts."""
    if not _can_read_memory():
        return []
    if _mongo_db is None:
        return []
    chat_id = _current_chat_id()
    if not chat_id:
        return []
    col = _mongo_db["sandy_facts"]
    fil = _user_filter(chat_id)
    try:
        if col.count_documents(fil) == 0:
            return []

        results = _vector_search(col, query, chat_id, n_results, {"usage_count": 1, "created_at": 1})

        if results is None:
            try:
                results = list(
                    col.find(
                        {**fil, "$text": {"$search": query}},
                        {"score": {"$meta": "textScore"}, "text": 1, "usage_count": 1, "created_at": 1},
                    )
                    .sort([("score", {"$meta": "textScore"})])
                    .limit(n_results)
                )
            except Exception:
                results = list(
                    col.find(fil, {"text": 1, "usage_count": 1, "created_at": 1})
                    .sort("importance_score", -1)
                    .limit(n_results)
                )

        if results:
            for r in results:
                new_usage = (r.get("usage_count") or 0) + 1
                score = _importance_score(new_usage, r.get("created_at"))
                col.update_one(
                    {"_id": r["_id"]},
                    {"$inc": {"usage_count": 1}, "$set": {"importance_score": score}},
                )
        return [r["text"] for r in results if r.get("text")]
    except Exception as e:
        print(f"[Memory] search_relevant_facts: {e}", flush=True)
        return []


def search_relevant_conversations(query: str, n_results: int = 3) -> List[str]:
    """Semantic search over the current user's conversation turns."""
    if not _can_read_memory():
        return []
    if _mongo_db is None:
        return []
    chat_id = _current_chat_id()
    if not chat_id:
        return []
    col = _mongo_db["sandy_conversations"]
    fil = _user_filter(chat_id)
    try:
        if col.count_documents(fil) == 0:
            return []

        results = _vector_search(col, query, chat_id, n_results, {})

        if results is None:
            try:
                results = list(
                    col.find(
                        {**fil, "$text": {"$search": query}},
                        {"score": {"$meta": "textScore"}, "text": 1},
                    )
                    .sort([("score", {"$meta": "textScore"})])
                    .limit(n_results)
                )
            except Exception:
                results = list(
                    col.find(fil, {"text": 1}).sort("ts", -1).limit(n_results)
                )

        return [r["text"] for r in results if r.get("text")]
    except Exception as e:
        print(f"[Memory] search_relevant_conversations: {e}", flush=True)
        return []


def search_relevant_summaries(query: str, chat_id: str, n_results: int = 3) -> List[str]:
    """Semantic search over conversation summaries in sandy_memories."""
    if _mongo_db is None or not chat_id:
        return []
    col = _mongo_db["sandy_memories"]
    fil = {"chat_id": str(chat_id), "label": "conversation_summary"}
    try:
        results = _vector_search(col, query, chat_id, n_results, {})
        if results is None:
            results = list(col.find(fil, {"summary": 1}).sort("created_at", -1).limit(n_results))
        return [r["summary"] for r in results if r.get("summary")]
    except Exception as exc:
        print(f"[chroma] search_relevant_summaries failed: {exc}", flush=True)
        return []


def chroma_stats() -> Dict[str, Any]:
    """Return counts for the current user, for health checks and debugging."""
    if not _can_read_memory():
        return {"path": "mongodb", "facts": 0, "conversations": 0}
    chat_id = _current_chat_id()
    facts_count = 0
    convs_count = 0
    if _mongo_db is not None and chat_id:
        fil = _user_filter(chat_id)
        try:
            facts_count = _mongo_db["sandy_facts"].count_documents(fil)
        except Exception:
            pass
        try:
            convs_count = _mongo_db["sandy_conversations"].count_documents(fil)
        except Exception:
            pass
    return {
        "path": "mongodb",
        "vector_search": _openai_client is not None,
        "facts": facts_count,
        "conversations": convs_count,
    }


__all__ = [
    "init_mongo_memory",
    "load_facts_to_chroma",
    "load_conversations_to_chroma",
    "search_relevant_facts",
    "search_relevant_conversations",
    "chroma_stats",
]
