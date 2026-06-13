"""Web API for the email tab — owner gets real Gmail, guests get demo data.

Same owner/guest pattern as productivity_api: every mutating endpoint is
owner-only, the guest GET returns obviously-fake messages so the tab looks
alive without leaking anything. All Gmail calls run inside the owner profile
context because the feature helpers refuse without it.

Endpoints:
  GET  /api/emails                       list recent inbox (owner real / guest demo)
  POST /api/emails/<id>/read             mark as read
  POST /api/emails/<id>/archive          archive (out of inbox)
  POST /api/emails/<id>/to-task          {kind: task|reminder, remind_at?} convert
  POST /api/emails/<id>/summarize        Arabic summary of the full body
  POST /api/emails/<id>/draft-reply      {instruction?} Sandy writes a draft reply
  POST /api/emails/<id>/send-reply       {body} actually send the reply
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

_DEMO_EMAILS = [
    {
        "id": "demo-e1",
        "sender": "فريق المشروع <team@example.com>",
        "subject": "تحديث أسبوعي — التقدم ممتاز",
        "snippet": "أهلاً! هذا الملخص الأسبوعي للمشروع، أنجزنا المرحلة الثانية و...",
        "date_iso": "2026-06-11T09:30:00+00:00",
        "unread": True,
    },
    {
        "id": "demo-e2",
        "sender": "أحمد <ahmad@example.com>",
        "subject": "بخصوص الاجتماع القادم",
        "snippet": "مرحبا، بدي أأكد موعد اجتماعنا يوم الخميس الساعة...",
        "date_iso": "2026-06-10T15:10:00+00:00",
        "unread": True,
    },
    {
        "id": "demo-e3",
        "sender": "نشرة تقنية <news@example.com>",
        "subject": "أحدث أخبار الذكاء الاصطناعي هذا الأسبوع",
        "snippet": "نماذج جديدة، أدوات مفتوحة المصدر، ومقالات مختارة...",
        "date_iso": "2026-06-09T07:00:00+00:00",
        "unread": False,
    },
]


def _gemini_text(prompt: str, max_tokens: int = 500) -> str:
    """One short model call via the in-house Gemini wrapper. '' on failure."""
    try:
        from app.integrations.azure_intent_client import AzureIntentClient

        return (
            AzureIntentClient().generate_text(
                prompt,
                response_mime_type="text/plain",
                max_output_tokens=max_tokens,
                temperature=0.4,
            )
            or ""
        ).strip()
    except Exception as e:
        print(f"[EmailsAPI] model call failed: {e}")
        return ""


def register_emails_api(app, mongo_db=None):
    @app.route("/api/emails", methods=["GET"])
    @require_auth
    def api_list_emails(claims):
        if claims.get("role") != "owner":
            return jsonify({"items": _DEMO_EMAILS, "demo": True}), 200
        # Never let a Gmail hiccup 500/hang the tab: always answer 200 with
        # an error field the UI can show next to a retry button.
        try:
            from app.features.gmail import list_inbox_emails

            with active_user_profile_context(_OWNER_PROFILE):
                items = list_inbox_emails(max_results=10)
            return jsonify({"items": items, "demo": False}), 200
        except Exception as e:  # noqa: BLE001
            print(f"[EmailsAPI] list failed: {type(e).__name__}: {e}")
            return jsonify(
                {"items": [], "demo": False, "error": f"{type(e).__name__}: {e}"}
            ), 200

    @app.route("/api/emails/<email_id>/read", methods=["POST"])
    @require_owner
    def api_email_mark_read(email_id, claims):
        from app.features.gmail import mark_email_read

        with active_user_profile_context(_OWNER_PROFILE):
            ok = mark_email_read(email_id)
        return jsonify({"ok": bool(ok)}), (200 if ok else 400)

    @app.route("/api/emails/<email_id>/archive", methods=["POST"])
    @require_owner
    def api_email_archive(email_id, claims):
        from app.features.gmail import archive_email

        with active_user_profile_context(_OWNER_PROFILE):
            ok = archive_email(email_id)
        return jsonify({"ok": bool(ok)}), (200 if ok else 400)

    @app.route("/api/emails/<email_id>/to-task", methods=["POST"])
    @require_owner
    def api_email_to_task(email_id, claims):
        """Turn an email into a task (default) or a reminder (kind=reminder)."""
        body = request.get_json(silent=True) or {}
        kind = (body.get("kind") or "task").strip().lower()
        from app.features.gmail import list_inbox_emails

        with active_user_profile_context(_OWNER_PROFILE):
            subject = (body.get("subject") or "").strip()
            sender = (body.get("sender") or "").strip()
            if not subject:
                for e in list_inbox_emails(max_results=25):
                    if e.get("id") == email_id:
                        subject = e.get("subject") or "(بدون عنوان)"
                        sender = e.get("sender") or ""
                        break
            if not subject:
                return jsonify({"error": "email_not_found"}), 404

            sender_short = sender.split("<", 1)[0].strip() or sender
            text = f"رد على إيميل: {subject}" + (f" — من {sender_short}" if sender_short else "")

            if kind == "reminder":
                remind_at = (body.get("remind_at") or "").strip()
                if not remind_at:
                    return jsonify({"error": "remind_at_required"}), 400
                from app.features.reminders_store import add_reminder

                res = add_reminder(text=text, remind_at_iso=remind_at)
                if res.get("success"):
                    return jsonify({"ok": True, "id": res.get("id", "")}), 200
                return jsonify({"error": res.get("error", "failed")}), 400

            from app.features.tasks_store import add_task

            tid = add_task(text, notes=f"gmail:{email_id}")
            if tid:
                return jsonify({"ok": True, "id": tid}), 200
            return jsonify({"error": "failed"}), 400

    @app.route("/api/emails/<email_id>/summarize", methods=["POST"])
    @require_owner
    def api_email_summarize(email_id, claims):
        from app.features.gmail import get_email_body

        with active_user_profile_context(_OWNER_PROFILE):
            text = get_email_body(email_id)
        if not text:
            return jsonify({"error": "empty_body"}), 404
        summary = _gemini_text(
            "لخّص هذا الإيميل بالعربية بثلاث جمل كحد أقصى، "
            "واذكر أي إجراء مطلوب مني إن وجد:\n\n" + text,
            max_tokens=300,
        )
        if not summary:
            return jsonify({"error": "summary_failed"}), 502
        return jsonify({"ok": True, "summary": summary}), 200

    @app.route("/api/emails/<email_id>/draft-reply", methods=["POST"])
    @require_owner
    def api_email_draft_reply(email_id, claims):
        """Sandy writes a reply draft; the owner edits then sends."""
        body = request.get_json(silent=True) or {}
        instruction = (body.get("instruction") or "").strip()
        from app.features.gmail import get_email_body

        with active_user_profile_context(_OWNER_PROFILE):
            text = get_email_body(email_id)
        if not text:
            return jsonify({"error": "empty_body"}), 404
        prompt = (
            "اكتب رداً مهذباً ومختصراً على هذا الإيميل بنفس لغته "
            "(عربي للعربي، إنجليزي للإنجليزي). أرجع نص الرد فقط بدون مقدمات.\n"
        )
        if instruction:
            prompt += f"توجيهات صاحب الرد: {instruction}\n"
        prompt += "\nالإيميل:\n" + text
        draft = _gemini_text(prompt, max_tokens=400)
        if not draft:
            return jsonify({"error": "draft_failed"}), 502
        return jsonify({"ok": True, "draft": draft}), 200

    @app.route("/api/emails/<email_id>/send-reply", methods=["POST"])
    @require_owner
    def api_email_send_reply(email_id, claims):
        body = request.get_json(silent=True) or {}
        reply_text = (body.get("body") or "").strip()
        if not reply_text:
            return jsonify({"error": "body_required"}), 400
        from app.features.gmail import reply_to_email, mark_email_read

        with active_user_profile_context(_OWNER_PROFILE):
            ok = reply_to_email(email_id, reply_text)
            if ok:
                mark_email_read(email_id)
        return jsonify({"ok": bool(ok)}), (200 if ok else 400)
