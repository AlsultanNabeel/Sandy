import functools
import os
import time
from datetime import datetime
from typing import Any, Dict

from app.agent.interests_tracker import get_proactive_interest_candidate
from app.agent.proactive_context import (
    build_proactive_need_hint,
)
from app.agent.memory import save_memory
from app.api.webhook import create_telegram_webhook_app
from app.utils import user_profiles
from app.utils.time import USER_TZ
from app.utils.user_profiles import active_user_profile_context

# Cooldown state for Heroku health alerts, so we don't repeat the same alert
# within 30 minutes.
_heroku_alert_state: Dict[str, Any] = {}


def send_proactive_insight(agent, telegram_bot, owner_chat_id: str) -> None:
    """Send a low-frequency proactive nudge grounded in repeated memory patterns."""
    if not owner_chat_id:
        return

    try:
        state = agent.memory.setdefault("sandy_state", {}) if isinstance(getattr(agent, "memory", None), dict) else {}
        last_sent_raw = str(state.get("last_proactive_insight_at") or "").strip()
        if last_sent_raw:
            try:
                last_sent = datetime.fromisoformat(last_sent_raw)
                if last_sent.tzinfo is None:
                    last_sent = last_sent.replace(tzinfo=USER_TZ)
                else:
                    last_sent = last_sent.astimezone(USER_TZ)
                if (datetime.now(USER_TZ) - last_sent).total_seconds() < 172800:
                    return
            except Exception:
                pass

        mongo_db = getattr(agent, "mongo_db", None)
        need_hint = build_proactive_need_hint(
            owner_chat_id,
            owner_chat_id,
            mongo_db=mongo_db,
            min_count=3,
        )
        interest_topic = get_proactive_interest_candidate(
            owner_chat_id,
            owner_chat_id,
            mongo_db=mongo_db,
            min_count=3,
        )

        need_reply = ""
        interest_reply = ""

        if interest_topic:
            try:
                from app.agent.executor.dispatch import execute_operational_action

                research_result = execute_operational_action(
                    "research",
                    {"query": f"أحدث وأفید شي عن {interest_topic}", "type": "general", "count": 3},
                    user_message=f"شاركيني شي مفيد عن {interest_topic}",
                    normalized_user_message=f"شاركيني شي مفيد عن {interest_topic}",
                    session={"chat_id": owner_chat_id, "user_id": owner_chat_id},
                    session_file=None,
                    mongo_db=mongo_db,
                    tasks_file=None,
                    create_chat_completion_fn=None,
                    save_session_fn=lambda *args, **kwargs: None,
                )
                if research_result.get("handled"):
                    interest_reply = str(research_result.get("reply") or "").strip()
            except Exception as exc:
                print(f"[Proactive] research failed: {exc}", flush=True)

        if need_hint:
            need_reply = f"🧠 لاحظت نمطاً متكرراً عندك: {need_hint}"

        if not need_reply and not interest_reply:
            return

        lines = ["🔔 Sandy — ملاحظة استباقية"]
        if need_reply:
            lines.append(need_reply)
        if interest_reply:
            lines.append(f"📚 شي قد يفيدك حول اهتماماتك:\n{interest_reply}")

        telegram_bot.send_message(owner_chat_id, "\n\n".join(lines), parse_mode=None)
        if isinstance(getattr(agent, "memory", None), dict):
            agent.memory.setdefault("sandy_state", {})["last_proactive_insight_at"] = datetime.now(USER_TZ).isoformat()
            if interest_topic:
                agent.memory.setdefault("sandy_state", {})["last_proactive_interest_topic"] = interest_topic
            save_memory(
                agent.memory,
                memory_file=agent.memory_file,
                mongo_db=agent.mongo_db,
            )
    except Exception as e:
        print(f"[Proactive] Error: {e}", flush=True)


def check_heroku_health(telegram_bot, owner_chat_id: str) -> None:
    """Check Heroku health every 5 min and alert the owner on crashes or memory issues."""
    if not owner_chat_id:
        return
    try:
        from app.tools.heroku_tool import (
            _API_KEY,
            get_logs,
            diagnose_logs,
            get_dyno_status,
        )

        if not _API_KEY():
            return

        # Gather data
        dyno_status = None
        log_text = None
        issues: list = []

        try:
            dyno_status = get_dyno_status()
        except Exception as _de:
            print(f"[HerokuAlert] dyno check failed: {_de}", flush=True)

        try:
            log_text = get_logs(lines=100)
            # M14a: only platform-level errors here (H10/H12/R14/R15/H14).
            # Python exceptions go through Sentry. Counting bare 'ERROR'
            # substrings used to spam the same alert every 5 min for hours
            # while the same line slowly aged out of the rolling 100-line
            # window.
            issues = diagnose_logs(log_text)
        except Exception as _le:
            print(f"[HerokuAlert] log check failed: {_le}", flush=True)

        if dyno_status and not dyno_status.get("all_up"):
            for d in dyno_status.get("crashed", []):
                entry = f"🔴 Dyno توقف: `{d}`"
                if entry not in issues:
                    issues.append(entry)

        if not issues:
            # All clear: reset the cooldown so the next real alert fires right away.
            _heroku_alert_state.clear()
            return

        # Cooldown: skip if we've seen the same issue labels in the last 30 min.
        # Compare as a sorted set of labels (not the raw list) so ordering or
        # one-off variations don't defeat the dedup.
        now = datetime.now(USER_TZ)
        issue_labels = sorted(set(issues))
        last_labels = _heroku_alert_state.get("labels")
        last_time = _heroku_alert_state.get("time")
        if (
            last_labels == issue_labels
            and last_time is not None
            and (now - last_time).total_seconds() < 1800
        ):
            return

        # Build and send the alert
        tail = ""
        if log_text:
            tail_lines = log_text.strip().splitlines()[-5:]
            tail = "\n```\n" + "\n".join(tail_lines)[:600] + "\n```"

        issues_text = "\n".join(f"• {i}" for i in issues)
        msg = (
            "🚨 *تنبيه Sandy — Heroku*\n\n"
            f"{issues_text}"
            f"{tail}\n\n"
            "خليني أصلح؟ _(ردّ: نعم / لا)_"
        )
        telegram_bot.send_message(owner_chat_id, msg, parse_mode="Markdown")
        print(f"[HerokuAlert] Alert sent: {issues}", flush=True)

        _heroku_alert_state["labels"] = issue_labels
        _heroku_alert_state["time"] = now

        # M5: record each label so the incident tracker can escalate to a
        # GitHub issue when the same one keeps firing.
        try:
            from app.agent import incident_tracker
            tail_evidence = "\n".join(log_text.strip().splitlines()[-10:]) \
                if log_text else ""
            for label in issue_labels:
                incident_tracker.maybe_escalate(
                    source="heroku",
                    category=label,
                    detail=f"Heroku alerter saw `{label}`.",
                    evidence=tail_evidence,
                )
        except Exception as _ie:
            print(f"[HerokuAlert] incident_tracker hook failed: {_ie}", flush=True)

    except Exception as e:
        print(f"[HerokuAlert] Unexpected error: {e}", flush=True)


def prepare_telegram_polling(telegram_bot):
    """Clear Telegram webhook before getUpdates (polling). Uses deleteWebhook API."""
    try:
        # Use delete_webhook so we can drop pending updates too. remove_webhook()
        # just calls set_webhook() and won't do that.
        telegram_bot.delete_webhook(drop_pending_updates=True)
        info = telegram_bot.get_webhook_info()
        url = getattr(info, "url", "") or ""
        pending = getattr(info, "pending_update_count", None)
        if url:
            print(
                f"[Telegram] Webhook still registered after delete: url={url!r} pending={pending}"
            )
        else:
            print("[Telegram] Webhook cleared, ready for local polling mode.")
    except Exception as e:
        print(
            f"[Telegram] Failed to delete webhook (polling may conflict with another bot/server): {e}"
        )


def run_telegram_polling(telegram_bot):
    prepare_telegram_polling(telegram_bot)
    while True:
        try:
            # timeout is the HTTP client wait; it must be longer than
            # long_polling_timeout plus some margin (see telebot.apihelper).
            telegram_bot.infinity_polling(
                skip_pending=False,
                timeout=60,
                long_polling_timeout=30,
                allowed_updates=["message", "callback_query"],
            )
        except Exception as e:
            msg = str(e)
            if (
                "Error code: 409" in msg
                or "terminated by other getUpdates request" in msg
            ):
                print("[Telegram] Polling conflict (409). Retrying in 5s...")
            elif "Read timed out" in msg or "timed out" in msg.lower():
                print(
                    "[Telegram] Network timeout to api.telegram.org, retrying in 5s..."
                )
            else:
                print(f"[Telegram] Polling crashed: {e}")
        time.sleep(5)


def set_telegram_webhook(
    telegram_bot,
    telegram_bot_token: str,
    app_url: str,
    webhook_path: str,
    telegram_secret_token: str = "",  # nosec B107
):
    if not telegram_bot_token or not app_url:
        print("[Webhook] TELEGRAM_BOT_TOKEN or APP_URL not set!")
        return

    webhook_url = app_url
    if not webhook_url.startswith("http"):
        webhook_url = "https://" + webhook_url
    webhook_url = webhook_url.rstrip("/") + webhook_path

    print(f"[Webhook] Setting webhook to: {webhook_url}")
    telegram_bot.delete_webhook(drop_pending_updates=False)
    telegram_bot.set_webhook(
        url=webhook_url,
        secret_token=telegram_secret_token if telegram_secret_token else None,
        allowed_updates=["message", "callback_query"],
    )


def run_telegram_webhook_server(
    telegram_bot,
    telegram_bot_token: str,
    app_url: str,
    webhook_path: str,
    telegram_secret_token: str,
    app,
    port: int | None = None,
):
    set_telegram_webhook(
        telegram_bot,
        telegram_bot_token,
        app_url,
        webhook_path,
        telegram_secret_token,
    )
    if port is None:
        port = int(os.environ.get("PORT", 8080))
    app.run(
        host="0.0.0.0",  # nosec B104 — required for Heroku/container networking
        port=port,
    )


def build_telegram_webhook_runtime(*, telegram_bot, mongo_db=None):
    telegram_secret_token = os.getenv("TELEGRAM_SECRET_TOKEN", "").strip()
    app_url = (
        os.getenv("APP_URL", "").strip()
        or os.getenv("RAILWAY_URL", "").strip()
        or os.getenv("HEROKU_APP_DEFAULT_DOMAIN_NAME", "").strip()
    )
    webhook_path = (
        f"/webhook/{telegram_secret_token}" if telegram_secret_token else "/webhook"
    )

    app = create_telegram_webhook_app(
        telegram_bot=telegram_bot,
        webhook_path=webhook_path,
        telegram_secret_token=telegram_secret_token,
        mongo_db=mongo_db,
    )

    return {
        "telegram_secret_token": telegram_secret_token,
        "app_url": app_url,
        "webhook_path": webhook_path,
        "app": app,
    }


def run_sandy_runtime(
    *,
    app_env: str,
    run_mode: str,
    openai_model: str,
    agent_memory_count: int,
    telegram_bot,
    telegram_bot_token: str,
    app_url: str,
    webhook_path: str,
    telegram_secret_token: str,
    app,
):
    print("=" * 60)
    print("🦞 Sandy Agent - 24/7 Intelligent Assistant")
    print("=" * 60)
    print(f"[Init] OpenAI Model: {openai_model}")
    print("[Init] Telegram Bot: Active")
    print("[Init] Scheduler: Active")
    print(f"[Init] Memory: Loaded ({agent_memory_count} conversations)")
    print("=" * 60)
    print("[Status] Ready! Listening for messages...")
    print("=" * 60)

    _app_url = (app_url or "").strip()
    _on_managed_platform = bool(
        os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("RAILWAY_PROJECT_ID")
        or os.getenv("DYNO")  # Heroku
    )
    if (
        app_env not in {"local", "dev", "development"}
        and run_mode != "polling"
        and not _app_url
        and not _on_managed_platform
    ):
        print(
            "[Telegram] ⚠️ You are in webhook mode but APP_URL is empty (typical local shell). "
            "Telegram will keep using the LAST webhook URL until something calls deleteWebhook. "
            "For local dev add to .env: APP_ENV=local   # or: RUN_MODE=polling"
        )

    _local_env = (app_env or "").strip().lower() in {"local", "dev", "development"}
    if _local_env or run_mode == "polling":
        print(
            "[Mode] Local development: Telegram polling (APP_ENV=local|dev or RUN_MODE=polling)"
        )
        run_telegram_polling(telegram_bot)
    else:
        print("[Mode] Production/Server: Webhook mode (APP_ENV=prod/RUN_MODE=webhook)")
        run_telegram_webhook_server(
            telegram_bot=telegram_bot,
            telegram_bot_token=telegram_bot_token,
            app_url=app_url,
            webhook_path=webhook_path,
            telegram_secret_token=telegram_secret_token,
            app=app,
        )


def configure_sandy_scheduler(
    *,
    scheduler,
    agent,
    telegram_bot,
    sandy_user_chat_id: str,
    check_reminders_fn,
):
    # Resolve OWNER_CHAT_ID from one canonical place at call time
    # (app.utils.user_profiles) so tests patch exactly that attribute and the
    # value never goes stale behind a module-load snapshot.
    owner_val = getattr(user_profiles, "OWNER_CHAT_ID", None)
    owner_chat_id = (owner_val or sandy_user_chat_id or "").strip()
    owner_profile = {
        "chat_id": owner_chat_id,
        "name": "",
        "relation": "owner",
        "tone": "casual",
        "permissions": "all",
    }

    def daily_briefing():
        """Send daily briefing at 10 AM."""
        try:
            if not owner_chat_id:
                print("[Briefing] OWNER_CHAT_ID missing — briefing skipped")
                return

            with active_user_profile_context(owner_profile):
                # نفس مسار المستخدم عند طلب «ملخص صباحي» — بدون مجازفة بمسار الدردشة العام.
                briefing_text = agent._build_morning_briefing()
                agent.memory.setdefault("sandy_state", {})["last_briefing_date"] = (
                    datetime.now(USER_TZ).strftime("%Y-%m-%d")
                )
                save_memory(
                    agent.memory,
                    memory_file=agent.memory_file,
                    mongo_db=agent.mongo_db,
                )

                telegram_bot.send_message(
                    owner_chat_id,
                    briefing_text,
                    parse_mode=None,
                )
        except Exception as e:
            print(f"[Briefing] Error: {e}")

    def _reminder_keyboard(reminder):
        """Snooze/done buttons under every delivered reminder."""
        import telebot.types as tg_types

        rid = str(reminder.get("id", "") or "")
        if not rid:
            return None
        kb = tg_types.InlineKeyboardMarkup()
        kb.row(
            tg_types.InlineKeyboardButton(
                "⏰ أجّل نص ساعة", callback_data=f"remsnz:{rid}:30"
            ),
            tg_types.InlineKeyboardButton(
                "🌅 بكرة", callback_data=f"remsnz:{rid}:1440"
            ),
        )
        kb.row(tg_types.InlineKeyboardButton("✅ تم", callback_data=f"remdone:{rid}"))
        return kb

    def run_owner_reminders():
        if not owner_chat_id:
            return None
        with active_user_profile_context(owner_profile):
            return check_reminders_fn(
                send_message_fn=telegram_bot.send_message,
                user_chat_id=owner_chat_id,
                keyboard_builder=_reminder_keyboard,
            )

    def log_memory_usage():
        """يطبع استهلاك الذاكرة كل ٥ دقايق — للكشف عن leaks."""
        try:
            import psutil
            import os as _os
            proc = psutil.Process(_os.getpid())
            rss_mb = proc.memory_info().rss / (1024 * 1024)
            print(f"[Memory] RSS={rss_mb:.1f}MB", flush=True)
        except Exception as e:
            print(f"[Memory] log failed: {e}", flush=True)

    def watch_important_emails():
        """تنبيه فوري بالإيميل المهم فقط — التصنيف بالذكاء، النشرات بتسكت."""
        if not owner_chat_id:
            return None
        try:
            from app.features.email_watch import check_new_important_emails

            with active_user_profile_context(owner_profile):
                return check_new_important_emails(
                    send_message_fn=telegram_bot.send_message,
                    user_chat_id=owner_chat_id,
                )
        except Exception as e:
            print(f"[EmailWatch] job failed: {e}")
            return None

    scheduler.add_job(daily_briefing, "cron", hour=6, minute=0)
    scheduler.add_job(run_owner_reminders, "interval", minutes=1)
    scheduler.add_job(watch_important_emails, "interval", minutes=5)
    scheduler.add_job(
        functools.partial(send_proactive_insight, agent, telegram_bot, owner_chat_id),
        "interval",
        days=2,
    )
    scheduler.add_job(log_memory_usage, "interval", minutes=5)
    scheduler.add_job(
        functools.partial(check_heroku_health, telegram_bot, owner_chat_id),
        "interval",
        minutes=5,
    )
