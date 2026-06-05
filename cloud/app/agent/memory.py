"""Persistent memory management: load/save to MongoDB or disk JSON."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from app.utils.files import read_json_file, write_json_file
from app.utils.user_profiles import (
    active_profile_allows_privileged_access,
    active_profile_is_guest,
)

# Defaults


def _default_memory() -> Dict[str, Any]:
    now = datetime.now().isoformat()
    return {
        "conversations": [],
        "facts": [],
        "reminders": [],
        "tasks": [],
        "sandy_state": {
            "mood": "happy",
            "last_user_message_time": now,
            "repeat_count": 0,
            "last_message": "",
            "snapped": False,
            "last_mood_change": now,
            "custom_facts": [],
            "user_persona_profile": "",
            "persona_profile": "",
            "home_city": "October City",
            "last_briefing_date": "",
            "context_summary": "",
            "last_synthesized": "",
            "predicted_intent": "",
        },
    }


def _default_session() -> Dict[str, Any]:
    return {"messages": [], "audit_trace": []}


# Load and save (sync)


def load_memory(
    memory_file: Optional[Path] = None, mongo_db: Optional[Any] = None
) -> Dict[str, Any]:
    """Load persistent memory from MongoDB (preferred) or disk JSON."""
    default_memory = _default_memory()

    if active_profile_is_guest():
        return default_memory

    if mongo_db is not None:
        try:
            memory_doc = mongo_db["memory"].find_one({"_id": "sandy_memory"})
            if memory_doc:
                memory_doc.pop("_id", None)
                print("[Memory] loaded from MongoDB")
                return memory_doc

            json_memory = read_json_file(memory_file, None)
            if isinstance(json_memory, dict):
                mongo_db["memory"].replace_one(
                    {"_id": "sandy_memory"},
                    {**json_memory, "_id": "sandy_memory"},
                    upsert=True,
                )
                print("[Memory] migrated JSON to MongoDB")
                return json_memory

            print("[Memory] MongoDB is source of truth (new memory)")
            return default_memory

        except Exception as e:
            print(f"[Memory] MongoDB error: {e}, falling back to JSON")

    memory_json = read_json_file(memory_file, None)
    if isinstance(memory_json, dict):
        print("[Memory] loaded from JSON file")
        return memory_json

    return default_memory


def save_memory(
    memory: Dict[str, Any],
    memory_file: Optional[Path] = None,
    mongo_db: Optional[Any] = None,
) -> None:
    """Save persistent memory to MongoDB (preferred) or disk JSON."""
    if not active_profile_allows_privileged_access():
        return

    if mongo_db is not None:
        try:
            mongo_db["memory"].replace_one(
                {"_id": "sandy_memory"},
                {**memory, "_id": "sandy_memory"},
                upsert=True,
            )
            return
        except Exception as e:
            print(f"[Memory] MongoDB save error: {e}, falling back to JSON")

    if write_json_file(memory_file, memory):
        print("[Memory] saved to JSON file")


def load_session(
    session_file: Optional[Path] = None, mongo_db: Optional[Any] = None
) -> Dict[str, Any]:
    """Load session memory from MongoDB (preferred) or disk JSON."""
    default_session = _default_session()

    if active_profile_is_guest():
        return default_session

    if mongo_db is not None:
        try:
            session_doc = mongo_db["sessions"].find_one({"_id": "current_session"})
            if session_doc:
                session_doc.pop("_id", None)
                print("[Session] loaded from MongoDB")
                return session_doc

            json_session = read_json_file(session_file, None)
            if isinstance(json_session, dict):
                mongo_db["sessions"].replace_one(
                    {"_id": "current_session"},
                    {**json_session, "_id": "current_session"},
                    upsert=True,
                )
                print("[Session] migrated JSON to MongoDB")
                return json_session

            print("[Session] MongoDB is source of truth (new session)")
            return default_session

        except Exception as e:
            print(f"[Session] MongoDB error: {e}, falling back to JSON")

    session_json = read_json_file(session_file, None)
    if isinstance(session_json, dict):
        print("[Session] loaded from JSON file")
        return session_json

    return default_session


def save_session(
    session: Dict[str, Any],
    session_file: Optional[Path] = None,
    mongo_db: Optional[Any] = None,
) -> None:
    """Save session memory to MongoDB (preferred) or disk JSON."""
    if not active_profile_allows_privileged_access():
        return

    if mongo_db is not None:
        try:
            if isinstance(session.get("messages"), list):
                session["messages"] = session["messages"][-20:]

            session_to_save = {
                "messages": session.get("messages", []),
                "pending_action": session.get("pending_action"),
                "task_aliases": session.get("task_aliases", {}),
                "completed_task_aliases": session.get("completed_task_aliases", {}),
                "image_state": session.get("image_state", {}),
                "gmail_last_list": session.get("gmail_last_list"),
                "email_send_draft": session.get("email_send_draft"),
                "last_search_results": session.get("last_search_results"),
                "last_action_context": session.get("last_action_context"),
                "audit_trace": (
                    session.get("audit_trace", [])[-50:]
                    if isinstance(session.get("audit_trace"), list)
                    else []
                ),
                "_last_created_task_id": session.get("_last_created_task_id", ""),
                "_last_created_task_text": session.get("_last_created_task_text", ""),
                "_id": "current_session",
            }
            mongo_db["sessions"].replace_one(
                {"_id": "current_session"}, session_to_save, upsert=True
            )
            return
        except Exception as e:
            print(f"[Session] MongoDB save error: {e}, falling back to JSON")

    if write_json_file(session_file, session):
        print("[Session] saved to JSON file")


