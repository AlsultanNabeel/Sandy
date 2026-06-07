"""Web API for reminders (Google Calendar) and tasks (Google Tasks).

The owner gets real CRUD straight against Google: add/edit/delete here change
the actual Google Calendar and Google Tasks, just like the Telegram bot. A
guest (visitor page) sees the same tabs but with obviously-fake demo data and
no mutating endpoints, so the page looks alive without exposing anything private.

All Google calls run inside ``active_user_profile_context`` because the
underlying helpers (``load_tasks``, ``add_calendar_event``, etc.) refuse unless
an owner profile is active. It's the same guard that protects the chat pipeline.
"""

from __future__ import annotations

from flask import jsonify, request

from app.api.auth_handlers import require_auth, require_owner
from app.utils.user_profiles import active_user_profile_context, OWNER_CHAT_ID

_OWNER_PROFILE = {
    "chat_id": OWNER_CHAT_ID,
    "name": "",
    "relation": "owner",
    "tone": "casual",
    "permissions": "all",
}

# Fake data so a visitor's tab mirrors the owner's layout without leaking the
# real reminders/tasks. The frontend hides every add/edit/delete control when
# ``demo`` is true.
_DEMO_REMINDERS = [
    {"id": "demo-r1", "text": "موعد طبيب الأسنان", "remind_at": "2026-06-05T16:00:00", "is_recurring": False},
    {"id": "demo-r2", "text": "اتصل بأحمد بخصوص المشروع", "remind_at": "2026-06-06T11:30:00", "is_recurring": False},
    {"id": "demo-r3", "text": "تمرين رياضة صباحي", "remind_at": "2026-06-07T07:00:00", "is_recurring": True},
]
_DEMO_TASKS = [
    {"id": "demo-t1", "text": "تجهيز العرض التقديمي", "done": False, "due_at": "2026-06-05T00:00:00"},
    {"id": "demo-t2", "text": "شراء هدية عيد الميلاد", "done": False, "due_at": ""},
]
_DEMO_TASKS_DONE = [
    {"id": "demo-d1", "text": "إرسال الفاتورة الشهرية", "done": True, "due_at": ""},
    {"id": "demo-d2", "text": "مراجعة التقرير الشهري", "done": True, "due_at": ""},
]


def register_productivity_api(app, mongo_db=None):
    # Reminders (Google Calendar)
    @app.route("/api/reminders", methods=["GET"])
    @require_auth
    def api_list_reminders(claims):
        if claims.get("role") != "owner":
            return jsonify({"items": _DEMO_REMINDERS, "demo": True}), 200
        from app.features.google_calendar import list_sandy_reminders
        with active_user_profile_context(_OWNER_PROFILE):
            items = list_sandy_reminders(max_results=50)
        slim = [
            {
                "id": r.get("id", ""),
                "text": r.get("text", ""),
                "remind_at": r.get("remind_at", ""),
                "is_recurring": bool(r.get("is_recurring", False)),
            }
            for r in items
            if r.get("id")
        ]
        return jsonify({"items": slim, "demo": False}), 200

    @app.route("/api/reminders", methods=["POST"])
    @require_owner
    def api_add_reminder(claims):
        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        remind_at = (body.get("remind_at") or "").strip()
        if not text or not remind_at:
            return jsonify({"error": "text_and_remind_at_required"}), 400
        from app.features.google_calendar import add_calendar_event
        with active_user_profile_context(_OWNER_PROFILE):
            res = add_calendar_event(
                title=text,
                start_iso=remind_at,
                description=f"Reminder created by Sandy: {text}",
                reminder_minutes=0,
            )
        if res.get("success"):
            return jsonify({"ok": True}), 200
        return jsonify({"error": res.get("error", "failed")}), 400

    @app.route("/api/reminders/<event_id>", methods=["DELETE"])
    @require_owner
    def api_delete_reminder(event_id, claims):
        from app.features.google_calendar import delete_calendar_event_by_id
        with active_user_profile_context(_OWNER_PROFILE):
            ok = delete_calendar_event_by_id(event_id)
        return jsonify({"ok": bool(ok)}), (200 if ok else 400)

    # Tasks (Google Tasks)
    @app.route("/api/tasks", methods=["GET"])
    @require_auth
    def api_list_tasks(claims):
        completed = request.args.get("completed") in ("1", "true", "yes")
        if claims.get("role") != "owner":
            demo = _DEMO_TASKS_DONE if completed else _DEMO_TASKS
            return jsonify({"items": demo, "demo": True}), 200
        from app.features.google_tasks import load_tasks, load_completed_tasks
        with active_user_profile_context(_OWNER_PROFILE):
            items = (
                load_completed_tasks(mongo_db=mongo_db)
                if completed
                else load_tasks(mongo_db=mongo_db)
            )
        slim = [
            {
                "id": t.get("id", ""),
                "text": t.get("text", ""),
                "done": bool(t.get("done", False)),
                "due_at": t.get("due_at") or t.get("due") or "",
            }
            for t in items
            if t.get("id")
        ]
        return jsonify({"items": slim, "demo": False}), 200

    @app.route("/api/tasks", methods=["POST"])
    @require_owner
    def api_add_task(claims):
        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        due = (body.get("due") or "").strip()
        if not text:
            return jsonify({"error": "text_required"}), 400
        from app.features.google_tasks import add_task
        with active_user_profile_context(_OWNER_PROFILE):
            tid = add_task(text, due_iso=due, mongo_db=mongo_db)
        if tid:
            return jsonify({"ok": True, "id": tid}), 200
        return jsonify({"error": "failed"}), 400

    @app.route("/api/tasks/<task_id>", methods=["PATCH"])
    @require_owner
    def api_update_task(task_id, claims):
        body = request.get_json(silent=True) or {}
        from app.features.google_tasks import (
            rename_task, complete_task, uncomplete_task,
        )
        ok = True
        with active_user_profile_context(_OWNER_PROFILE):
            new_text = (body.get("text") or "").strip()
            if new_text:
                ok = rename_task(task_id, new_text, mongo_db=mongo_db) and ok
            if "done" in body:
                if body.get("done"):
                    ok = complete_task(task_id, mongo_db=mongo_db) and ok
                else:
                    ok = uncomplete_task(task_id, mongo_db=mongo_db) and ok
        return jsonify({"ok": bool(ok)}), (200 if ok else 400)

    @app.route("/api/tasks/<task_id>", methods=["DELETE"])
    @require_owner
    def api_delete_task(task_id, claims):
        from app.features.google_tasks import delete_task
        with active_user_profile_context(_OWNER_PROFILE):
            ok = delete_task(task_id, mongo_db=mongo_db)
        return jsonify({"ok": bool(ok)}), (200 if ok else 400)
