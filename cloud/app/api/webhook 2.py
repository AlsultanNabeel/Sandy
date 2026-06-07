from flask import Flask, abort, request
import telebot


def create_telegram_webhook_app(*, telegram_bot, webhook_path: str, telegram_secret_token: str = ""):
    app = Flask(__name__)

    @app.route(webhook_path, methods=["POST"])
    def telegram_webhook():
        if telegram_secret_token:
            header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if header_token != telegram_secret_token:
                abort(403)

        try:
            telegram_bot.process_new_updates([
                telebot.types.Update.de_json(request.stream.read().decode("utf-8"))
            ])
        except Exception as e:
            print(f"[Webhook] ❌ Error: {e}")

        return "OK", 200

    return app
