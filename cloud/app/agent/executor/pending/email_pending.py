from typing import Any, Dict

from app.agent.pending import clear_pending_action


_EMAIL_SEND_WORDS = {
    "ارسل",
    "ارسله",
    "ارسلها",
    "أرسل",
    "أرسله",
    "أرسلها",
    "ارسلي",
    "أرسلي",
    "send",
    "يلا ارسل",
    "ابعت",
    "ابعثه",
    "اه",
    "أه",
    "نعم",
    "ايوه",
    "ok",
    "okay",
    "تمام",
    "اكيد",
}
_EMAIL_DRAFT_WORDS = {
    "مسودة",
    "احفظ",
    "احفظه",
    "احفظها",
    "draft",
    "save",
    "حفظ",
    "خزن",
    "خزّن",
}


def _email_inline_markup():
    try:
        import telebot.types as _tg

        markup = _tg.InlineKeyboardMarkup()
        markup.row(
            _tg.InlineKeyboardButton("📨 ارسل", callback_data="email_send"),
            _tg.InlineKeyboardButton("💾 مسودة", callback_data="email_draft"),
        )
        markup.row(
            _tg.InlineKeyboardButton("✏️ تعديل", callback_data="email_edit"),
            _tg.InlineKeyboardButton("❌ الغاء", callback_data="email_cancel"),
        )
        return markup
    except Exception:
        return None


def _handle_email_confirm_pending(
    user_message: str,
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    save_session_fn,
    create_chat_completion_fn=None,
) -> Dict[str, Any]:
    """يعالج رسائل المستخدم أثناء انتظار تأكيد إرسال إيميل أو تعديله."""
    from app.agent.email_resolve import (
        _parse_email_field_edit,
        _render_email_preview,
        _is_draft_request,
    )

    msg_l = (user_message or "").strip().lower()
    to = str(pending.get("to", "")).strip()
    subject = str(pending.get("subject", "")).strip()
    body = str(pending.get("body", "")).strip()

    # ── ارسل مباشرة ─────────────────────────────────────────────────────────
    if msg_l in _EMAIL_SEND_WORDS or "ارسل" in msg_l:
        clear_pending_action(session)
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        try:
            from app.features.gmail import send_email

            send_email(to, subject, body)
            return {"handled": True, "reply": f"✅ تم إرسال الإيميل إلى {to}."}
        except Exception as e:
            try:
                from app.utils.google_oauth_errors import (
                    user_message_for_google_auth_failure,
                )

                return {
                    "handled": True,
                    "reply": user_message_for_google_auth_failure(e),
                }
            except Exception:
                return {"handled": True, "reply": f"⚠️ ما قدرت أرسل: {e}"}

    # ── مسودة مباشرة ─────────────────────────────────────────────────────────
    if msg_l in _EMAIL_DRAFT_WORDS or _is_draft_request(user_message):
        clear_pending_action(session)
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        try:
            from app.features.gmail import create_gmail_draft

            create_gmail_draft(to, subject, body)
            return {"handled": True, "reply": "💾 تم حفظ الإيميل كمسودة."}
        except Exception as e:
            try:
                from app.utils.google_oauth_errors import (
                    user_message_for_google_auth_failure,
                )

                return {
                    "handled": True,
                    "reply": user_message_for_google_auth_failure(e),
                }
            except Exception:
                return {"handled": True, "reply": f"⚠️ ما قدرت أحفظ: {e}"}

    # ── تعديل حقل ────────────────────────────────────────────────────────────
    edit = _parse_email_field_edit(
        user_message,
        to,
        subject,
        body,
        create_chat_completion_fn=create_chat_completion_fn,
    )
    if edit and edit.get("field"):
        field = edit["field"]
        op = edit.get("op", "replace")
        value = str(edit.get("value", "")).strip()
        if field == "to":
            to = value
        elif field == "subject":
            subject = value if op == "replace" else f"{subject} {value}".strip()
        elif field == "body":
            body = value if op == "replace" else f"{body}\n{value}".strip()

        updated_pending = dict(pending)
        updated_pending.update({"to": to, "subject": subject, "body": body})
        session["pending_action"] = updated_pending
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)

        preview = _render_email_preview(to, subject, body)
        markup = _email_inline_markup()
        result = {"handled": True, "reply": preview}
        if markup:
            result["reply_markup"] = markup
        return result

    return {"handled": False}


def _handle_await_email_body(
    user_message: str,
    pending: dict,
    *,
    session: dict,
    session_file,
    mongo_db,
    save_session_fn,
) -> dict:
    """يكمّل إيميل كان ناقصه النص — المستخدم رد بالنص بعد سؤال 'اكتب الرسالة'."""
    from app.agent.executor.helpers import is_cancellation
    from app.agent.pending import create_pending_action, clear_pending_action

    if is_cancellation(user_message):
        clear_pending_action(session)
        save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
        return {"handled": True, "reply": "تمام، ألغيت الإيميل."}

    body = (user_message or "").strip()
    if not body:
        return {"handled": True, "reply": "النص فارغ. اكتب محتوى الرسالة."}

    to = str(pending.get("to", "")).strip()
    subject = str(pending.get("subject", "")).strip()

    # Upgrade to confirm_send pending with body filled
    session["pending_action"] = create_pending_action({
        "type": "email",
        "action": "confirm_send",
        "to": to,
        "subject": subject,
        "body": body,
        "confirmation_status": "pending",
    })
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)

    try:
        from app.agent.email_resolve import _render_email_preview
        preview = _render_email_preview(to, subject, body)
    except Exception:
        preview = f"إلى: {to}\nالموضوع: {subject}\n\n{body}"

    markup = _email_inline_markup()
    result = {"handled": True, "reply": preview}
    if markup:
        result["reply_markup"] = markup
    return result


def _exec_email_send(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    save_session_fn,
) -> Dict[str, Any]:
    to = str(pending.get("to", "")).strip()
    subject = str(pending.get("subject", "")).strip()
    body = str(pending.get("body", "")).strip()
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    if not to:
        return {
            "handled": True,
            "reply": "⚠️ ما في مستلم. حدّد عنوان البريد وجرّب مرة ثانية.",
        }
    try:
        from app.features.gmail import send_email

        send_email(to, subject, body)
        return {"handled": True, "reply": f"✅ تم إرسال الإيميل إلى {to}."}
    except Exception as e:
        try:
            from app.utils.google_oauth_errors import (
                user_message_for_google_auth_failure,
            )

            return {"handled": True, "reply": user_message_for_google_auth_failure(e)}
        except Exception:
            return {"handled": True, "reply": f"⚠️ ما قدرت أرسل الإيميل: {e}"}


def _exec_email_draft(
    pending: Dict[str, Any],
    *,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    save_session_fn,
) -> Dict[str, Any]:
    to = str(pending.get("to", "")).strip()
    subject = str(pending.get("subject", "")).strip()
    body = str(pending.get("body", "")).strip()
    clear_pending_action(session)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    try:
        from app.features.gmail import create_gmail_draft

        create_gmail_draft(to, subject, body)
        return {"handled": True, "reply": "💾 تم حفظ الإيميل كمسودة."}
    except Exception as e:
        try:
            from app.utils.google_oauth_errors import (
                user_message_for_google_auth_failure,
            )

            return {"handled": True, "reply": user_message_for_google_auth_failure(e)}
        except Exception:
            return {"handled": True, "reply": f"⚠️ ما قدرت أحفظ المسودة: {e}"}
