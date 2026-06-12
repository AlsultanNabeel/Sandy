"""Studio web APIs: robot dashboard, project-builder list, GitHub issues,
and the unified search box.

Owner/guest split everywhere, same as productivity_api: guests see demo
payloads with `demo: true` and no mutating endpoints; the owner gets the
real thing inside the owner profile context.

Endpoints:
  GET  /api/robot/status        live robot state (online, mood, telemetry)
  POST /api/robot/command       {type: mood|servo|buzzer, value}
  GET  /api/projects            project-builder tasks (done + in-flight)
  GET  /api/github/issues       repo issues, ?state=open|closed|all
  GET  /api/search?q=...        one box across tasks/reminders/plans/emails
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

_DEMO_ROBOT = {
    "online": True,
    "mood": "happy",
    "status": {"distance_cm": 42, "uptime_min": 128},
    "demo": True,
}

_DEMO_PROJECTS = [
    {
        "id": "demo-p1",
        "type": "build_project",
        "status": "done",
        "description": "موقع شخصي بسيط مع صفحة أعمال",
        "repo": "sandy-demo/portfolio",
        "branch": "main",
        "updated_at": "2026-06-08T12:00:00",
    },
    {
        "id": "demo-p2",
        "type": "build_project",
        "status": "processing",
        "description": "تطبيق قائمة قراءة مع تتبع صفحات",
        "repo": "sandy-demo/reading-list",
        "branch": "sandy/reading-list",
        "updated_at": "2026-06-11T09:00:00",
    },
]

_DEMO_ISSUES = [
    {"number": 12, "title": "تحسين سرعة فتح الصفحة الرئيسية", "state": "open",
     "labels": ["enhancement"], "html_url": "", "created_at": "2026-06-09T08:00:00Z", "comments": 2},
    {"number": 9, "title": "خطأ في عرض الصور على الموبايل", "state": "open",
     "labels": ["bug"], "html_url": "", "created_at": "2026-06-07T10:00:00Z", "comments": 5},
]

_DEMO_SEARCH = {
    "tasks": [{"id": "demo-t1", "text": "تجهيز العرض التقديمي"}],
    "reminders": [{"id": "demo-r1", "text": "موعد طبيب الأسنان", "remind_at": "2026-06-15T16:00:00"}],
    "plans": [{"topic": "خطة تعلم البرمجة", "summary": "ثلاث مراحل خلال شهرين"}],
    "emails": [{"id": "demo-e2", "subject": "بخصوص الاجتماع القادم", "sender": "أحمد"}],
}


def _list_project_tasks(max_results: int = 50):
    """Project-builder tasks from Redis (sandy_sa:task:*), newest first."""
    from app.agent.project_builder import _redis as pb_redis

    client = pb_redis.get_client()
    if client is None:
        return []
    items = []
    try:
        for key in client.scan_iter(match="sandy_sa:task:*", count=200):
            k = key.decode() if isinstance(key, bytes) else str(key)
            # Skip sub-keys like sandy_sa:task:<id>:resume
            if k.count(":") != 2:
                continue
            try:
                raw = client.hgetall(k)
            except Exception:
                continue
            doc = {}
            for kk, vv in (raw or {}).items():
                kk = kk.decode() if isinstance(kk, bytes) else str(kk)
                vv = vv.decode() if isinstance(vv, bytes) else str(vv)
                doc[kk] = vv
            if not doc:
                continue
            items.append(
                {
                    "id": doc.get("task_id", k.rsplit(":", 1)[-1]),
                    "type": doc.get("type", ""),
                    "status": doc.get("status", ""),
                    "description": doc.get("description", ""),
                    "repo": doc.get("repo", ""),
                    "branch": doc.get("branch", ""),
                    "attempts": doc.get("attempts", "0"),
                    "updated_at": doc.get("last_active", "") or doc.get("enqueued_at", ""),
                }
            )
    except Exception as e:  # noqa: BLE001
        print(f"[StudioAPI] project scan failed: {e}")
        return []
    items.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
    return items[:max_results]


def register_studio_api(app, mongo_db=None):
    # ── Robot dashboard ──────────────────────────────────────────────────
    @app.route("/api/robot/status", methods=["GET"])
    @require_auth
    def api_robot_status(claims):
        if claims.get("role") != "owner":
            return jsonify(_DEMO_ROBOT), 200
        try:
            from app.integrations.sandy_device import get_sandy_device_client

            device = get_sandy_device_client()
            online = bool(device and device.available and device.is_online())
            status = device.get_full_status() if device else {}
            return jsonify(
                {
                    "online": online,
                    "mood": (status or {}).get("mood", ""),
                    "status": status or {},
                    "demo": False,
                }
            ), 200
        except Exception as e:  # noqa: BLE001
            print(f"[StudioAPI] robot status failed: {e}")
            return jsonify({"online": False, "mood": "", "status": {}, "demo": False}), 200

    @app.route("/api/robot/command", methods=["POST"])
    @require_owner
    def api_robot_command(claims):
        body = request.get_json(silent=True) or {}
        cmd = (body.get("type") or "").strip().lower()
        value = body.get("value")
        try:
            from app.integrations.sandy_device import get_sandy_device_client

            device = get_sandy_device_client()
            if not device or not device.available:
                return jsonify({"error": "robot_offline"}), 503
            if cmd == "mood":
                ok = device.set_mood(str(value or "").strip().lower())
            elif cmd == "servo":
                ok = device.set_servo(int(value))
            elif cmd == "buzzer":
                ok = device.play_buzzer(str(value or "alert").strip().lower())
            else:
                return jsonify({"error": "bad_command"}), 400
            return jsonify({"ok": bool(ok)}), (200 if ok else 502)
        except Exception as e:  # noqa: BLE001
            print(f"[StudioAPI] robot command failed: {e}")
            return jsonify({"error": "failed"}), 502

    # ── Project builder ──────────────────────────────────────────────────
    @app.route("/api/projects", methods=["GET"])
    @require_auth
    def api_list_projects(claims):
        if claims.get("role") != "owner":
            return jsonify({"items": _DEMO_PROJECTS, "demo": True}), 200
        items = _list_project_tasks()
        return jsonify({"items": items, "demo": False}), 200

    # ── Brainstorm plans ─────────────────────────────────────────────────
    @app.route("/api/plans", methods=["GET"])
    @require_auth
    def api_list_plans(claims):
        if claims.get("role") != "owner":
            return jsonify(
                {
                    "items": [
                        {
                            "id": "demo-pl1",
                            "topic": "خطة تعلم البرمجة",
                            "summary": "ثلاث مراحل خلال شهرين مع مشاريع صغيرة",
                            "finished_at": "2026-06-05T20:00:00",
                            "plan_text": "## الهدف\nتعلم أساسيات البرمجة...\n(نموذج تجريبي)",
                        }
                    ],
                    "demo": True,
                }
            ), 200
        items = []
        try:
            if mongo_db is not None:
                owner = str(OWNER_CHAT_ID or "")
                for d in (
                    mongo_db["sandy_brainstorms"]
                    .find({"status": "done", "chat_id": {"$in": [owner, int(owner) if owner.isdigit() else owner]}})
                    .sort("finished_at", -1)
                    .limit(30)
                ):
                    items.append(
                        {
                            "id": str(d.get("_id", "")),
                            "topic": d.get("topic", ""),
                            "summary": d.get("summary", ""),
                            "finished_at": str(d.get("finished_at", "") or ""),
                            "plan_text": d.get("plan_text", ""),
                        }
                    )
        except Exception as e:  # noqa: BLE001
            print(f"[StudioAPI] plans list failed: {e}")
        return jsonify({"items": items, "demo": False}), 200

    # ── GitHub issues ────────────────────────────────────────────────────
    @app.route("/api/github/issues", methods=["GET"])
    @require_auth
    def api_github_issues(claims):
        if claims.get("role") != "owner":
            return jsonify({"items": _DEMO_ISSUES, "demo": True}), 200
        state = (request.args.get("state") or "open").strip().lower()
        if state not in {"open", "closed", "all"}:
            state = "open"
        from app.integrations.github_api import list_issues

        result = list_issues(state=state)
        if not result.get("ok"):
            return jsonify({"items": [], "error": result.get("error", "failed")}), 200
        return jsonify({"items": result.get("items", []), "demo": False}), 200

    # ── Unified search ───────────────────────────────────────────────────
    @app.route("/api/search", methods=["GET"])
    @require_auth
    def api_unified_search(claims):
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"error": "q_required"}), 400
        if claims.get("role") != "owner":
            return jsonify({**_DEMO_SEARCH, "demo": True}), 200

        ql = q.lower()
        out = {"tasks": [], "reminders": [], "plans": [], "emails": [], "demo": False}

        with active_user_profile_context(_OWNER_PROFILE):
            try:
                from app.features.tasks_store import load_tasks, load_completed_tasks

                for t in load_tasks() + load_completed_tasks():
                    hay = f"{t.get('text','')} {t.get('notes','')} {t.get('project','')}".lower()
                    if ql in hay:
                        out["tasks"].append(
                            {"id": t["id"], "text": t["text"], "done": t["done"]}
                        )
            except Exception as e:  # noqa: BLE001
                print(f"[StudioAPI] search tasks failed: {e}")

            try:
                from app.features.reminders_store import load_reminders

                for r in load_reminders(max_results=100):
                    if ql in (r.get("text", "") or "").lower():
                        out["reminders"].append(
                            {"id": r["id"], "text": r["text"], "remind_at": r["remind_at"]}
                        )
            except Exception as e:  # noqa: BLE001
                print(f"[StudioAPI] search reminders failed: {e}")

            try:
                if mongo_db is not None:
                    for d in mongo_db["sandy_brainstorms"].find(
                        {"status": "done"}, {"topic": 1, "summary": 1, "plan_text": 1}
                    ).limit(100):
                        hay = f"{d.get('topic','')} {d.get('summary','')} {d.get('plan_text','')}".lower()
                        if ql in hay:
                            out["plans"].append(
                                {"topic": d.get("topic", ""), "summary": d.get("summary", "")}
                            )
            except Exception as e:  # noqa: BLE001
                print(f"[StudioAPI] search plans failed: {e}")

            try:
                from app.features.gmail import list_inbox_emails

                for e in list_inbox_emails(max_results=20):
                    hay = f"{e.get('subject','')} {e.get('sender','')} {e.get('snippet','')}".lower()
                    if ql in hay:
                        out["emails"].append(
                            {
                                "id": e["id"],
                                "subject": e.get("subject", ""),
                                "sender": e.get("sender", ""),
                            }
                        )
            except Exception as e:  # noqa: BLE001
                print(f"[StudioAPI] search emails failed: {e}")

        return jsonify(out), 200
