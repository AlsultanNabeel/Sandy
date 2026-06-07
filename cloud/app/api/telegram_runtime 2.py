import os
import time
from app.api.webhook import create_telegram_webhook_app


def prepare_telegram_polling(telegram_bot):
    try:
        telegram_bot.remove_webhook()
        print("[Telegram] Webhook removed for local polling mode.")
    except Exception as e:
        print(f"[Telegram] Failed to remove webhook: {e}")


def run_telegram_polling(telegram_bot):
    prepare_telegram_polling(telegram_bot)
    while True:
        try:
            telegram_bot.infinity_polling(
                skip_pending=False,
                timeout=30,
                long_polling_timeout=30,
                allowed_updates=["message", "callback_query"],
            )
        except Exception as e:
            msg = str(e)
            if "Error code: 409" in msg or "terminated by other getUpdates request" in msg:
                print("[Telegram] ⚠️ Polling conflict (409). Retrying in 5s...")
            else:
                print(f"[Telegram] ❌ Polling crashed: {e}")
        time.sleep(5)


def set_telegram_webhook(
    telegram_bot,
    telegram_bot_token: str,
    railway_url: str,
    webhook_path: str,
    telegram_secret_token: str = "",
):
    if not telegram_bot_token or not railway_url:
        print("[Webhook] TELEGRAM_BOT_TOKEN or RAILWAY_URL not set!")
        return

    webhook_url = railway_url
    if not webhook_url.startswith("http"):
        webhook_url = "https://" + webhook_url
    webhook_url = webhook_url.rstrip("/") + webhook_path

    print(f"[Webhook] Setting webhook to: {webhook_url}")
    telegram_bot.remove_webhook()
    telegram_bot.set_webhook(
        url=webhook_url,
        secret_token=telegram_secret_token if telegram_secret_token else None,
        allowed_updates=["message", "callback_query"],
    )


def run_telegram_webhook_server(
    telegram_bot,
    telegram_bot_token: str,
    railway_url: str,
    webhook_path: str,
    telegram_secret_token: str,
    app,
    port: int | None = None,
):
    set_telegram_webhook(
        telegram_bot,
        telegram_bot_token,
        railway_url,
        webhook_path,
        telegram_secret_token,
    )
    if port is None:
        port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def build_telegram_webhook_runtime(*, telegram_bot):
    telegram_secret_token = os.getenv("TELEGRAM_SECRET_TOKEN", "").strip()
    railway_url = os.getenv("RAILWAY_URL", "").strip()
    webhook_path = f"/webhook/{telegram_secret_token}" if telegram_secret_token else "/webhook"

    app = create_telegram_webhook_app(
        telegram_bot=telegram_bot,
        webhook_path=webhook_path,
        telegram_secret_token=telegram_secret_token,
    )

    return {
        "telegram_secret_token": telegram_secret_token,
        "railway_url": railway_url,
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
    railway_url: str,
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

    if app_env == "local" or run_mode == "polling":
        print("[Mode] Local development: Telegram polling mode (APP_ENV=local or RUN_MODE=polling)")
        run_telegram_polling(telegram_bot)
    else:
        print("[Mode] Production/Server: Webhook mode (APP_ENV=prod/RUN_MODE=webhook)")
        run_telegram_webhook_server(
            telegram_bot=telegram_bot,
            telegram_bot_token=telegram_bot_token,
            railway_url=railway_url,
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
    def daily_briefing():
        """Send daily briefing at 9 AM."""
        try:
            briefing = agent.think("قدملي ملخص يومي عن اللي صار")
            briefing_text = briefing.get("text", "") if isinstance(briefing, dict) else str(briefing)

            telegram_bot.send_message(
                sandy_user_chat_id,
                f"[morning] صباح الخير  ! ☀️\n\n{briefing_text}",
                parse_mode=None,
            )
        except Exception as e:
            print(f"[Briefing] Error: {e}")

    scheduler.add_job(daily_briefing, "cron", hour=9, minute=0)
    scheduler.add_job(
        lambda: check_reminders_fn(
            send_message_fn=telegram_bot.send_message,
            user_chat_id=sandy_user_chat_id,
        ),
        "interval",
        minutes=1,
    )
