import json
import logging
import os
import hmac
import hashlib
import time
from datetime import datetime, timezone

from flask import Flask, abort, jsonify, request
from flask_cors import CORS
import telebot
import uuid

from app.agent.project_builder import _redis as sa_redis
from app.utils import trace as trace_ctx
from app.utils import metrics as metrics
from app.utils import metrics_push as metrics_push

from app.utils.thread_pool import sandy_executor

from app.agent.semantic_memory import semantic_memory_stats
from app.utils.error_tracking import log_unhandled_exception
from app.tools.heroku_tool import analyze_build_logs, format_build_alert

logger = logging.getLogger(__name__)


def create_telegram_webhook_app(
    *,
    telegram_bot,
    webhook_path: str,
    telegram_secret_token: str = "",
    mongo_db=None,
    semantic_memory_stats_fn=semantic_memory_stats,
):
    # Imported here (not at module top) so importing this module stays free of
    # the PyJWT dependency until the app is actually built. The decorators below
    # are applied when this factory runs, which is after import.
    from app.api.auth_handlers import (
        approve_access_request,
        check_owner_password,
        check_rate_limit,
        deny_access_request,
        get_access_request,
        make_token,
        require_auth,
        store_access_request,
    )

    app = Flask(__name__)
    CORS(app, resources={r"/api/*": {"origins": os.getenv("FRONTEND_URL", "*")}})

    # Start the Grafana Cloud metrics pusher. It's a no-op if the env vars
    # aren't set, and the thread is daemonized so it dies with the dyno.
    try:
        metrics_push.start_metrics_pusher()
    except Exception as _exc:
        # Pushing metrics is best-effort, don't let it fail startup.
        print(f"[metrics_push] start failed: {_exc}")

    @app.route("/health", methods=["GET"])
    def health():
        mongo_status = {"ok": False, "available": mongo_db is not None}
        if mongo_db is not None:
            try:
                mongo_db.command("ping")
                mongo_status.update(
                    {"ok": True, "database": getattr(mongo_db, "name", None)}
                )
            except Exception as exc:
                mongo_status["error"] = str(exc)

        chroma_status = {"ok": False}
        try:
            chroma_data = semantic_memory_stats_fn() if callable(semantic_memory_stats_fn) else {}
            chroma_status.update({"ok": True, **(chroma_data or {})})
        except Exception as exc:
            chroma_status["error"] = str(exc)

        # Check Telegram with a short timeout and treat it as non-fatal: a
        # slow or flaky Telegram API shouldn't make Heroku mark the dyno
        # unhealthy. get_me() takes no timeout arg, so we tighten telebot's
        # global socket timeouts around it and put them back after.
        telegram_status = {"ok": False, "fatal": False}
        _prev_connect = getattr(telebot.apihelper, "CONNECT_TIMEOUT", None)
        _prev_read = getattr(telebot.apihelper, "READ_TIMEOUT", None)
        try:
            telebot.apihelper.CONNECT_TIMEOUT = 3
            telebot.apihelper.READ_TIMEOUT = 3
            me = telegram_bot.get_me()
            telegram_status.update(
                {
                    "ok": True,
                    "username": getattr(me, "username", None),
                    "id": getattr(me, "id", None),
                }
            )
        except Exception as exc:
            # Mark Telegram as degraded but keep the dyno healthy.
            telegram_status["error"] = str(exc)
            telegram_status["degraded"] = True
        finally:
            if _prev_connect is not None:
                telebot.apihelper.CONNECT_TIMEOUT = _prev_connect
            if _prev_read is not None:
                telebot.apihelper.READ_TIMEOUT = _prev_read

        # Only the critical backing stores gate health. A slow Telegram shows
        # up in the payload but doesn't fail the check.
        overall_ok = (
            mongo_status.get("ok")
            and chroma_status.get("ok")
        )
        return jsonify(
            {
                "ok": bool(overall_ok),
                "mongo": mongo_status,
                "chroma": chroma_status,
                "telegram": telegram_status,
            }
        ), (200 if overall_ok else 503)

    @app.route(webhook_path, methods=["POST"])
    def telegram_webhook():
        t_ingress = time.perf_counter()

        metrics.inc_webhook_ingress()

        if telegram_secret_token:
            header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if header_token != telegram_secret_token:
                abort(403)

        raw_body = request.get_data(as_text=True) or ""
        chat_id = None
        if raw_body:
            try:
                payload = json.loads(raw_body)
                chat = payload.get("message", {}).get("chat", {})
                chat_id = chat.get("id")
                if chat_id is None:
                    callback_message = payload.get("callback_query", {}).get(
                        "message", {}
                    )
                    chat_id = callback_message.get("chat", {}).get("id")
            except Exception:
                pass

        try:
            update = telebot.types.Update.de_json(raw_body)
        except Exception as e:
            print(f"[Webhook] Failed to parse update: {e}")
            log_unhandled_exception(mongo_db, e, chat_id=chat_id, source="webhook")
            return "OK", 200

        # Dedup via Redis: webhook_seen_setnx returns True if this update_id is
        # new, False if we've seen it. If Redis is down it fails open (returns
        # True) so we keep processing rather than block.
        try:
            update_id = getattr(update, "update_id", None)
            if update_id is not None:
                if not sa_redis.webhook_seen_setnx(update_id, ttl=60 * 60):
                    print(f"[Webhook] Duplicate update_id={update_id}, skipping")
                    metrics.inc_webhook_dedup()
                    return "OK", 200
        except Exception:
            # Don't block processing if the dedup check itself errors.
            pass

        # One trace id per ingress, stored on the thread-local context so
        # handlers can include it in their logs and errors.
        trace_id = uuid.uuid4().hex

        def _process():
            try:
                trace_ctx.set_trace_id(trace_id)
                # Also stash it on the update so handlers that inspect the
                # Update can read it.
                try:
                    setattr(update, "_trace_id", trace_id)
                except Exception:
                    pass

                telegram_bot.process_new_updates([update])
            except Exception as e:
                print(f"[Webhook] Error: {e}")
                log_unhandled_exception(mongo_db, e, chat_id=chat_id, source="webhook", extra={"trace_id": trace_id})
            finally:
                trace_ctx.clear_trace_id()

        sandy_executor.submit(_process)
        print(
            f"[Webhook] ingress→dispatch: {(time.perf_counter()-t_ingress)*1000:.0f}ms chat_id={chat_id} trace_id={trace_id}"
        )
        try:
            metrics.observe_webhook_duration(time.perf_counter() - t_ingress)
        except Exception:
            pass
        return "OK", 200

    # GitHub Actions build webhook

    @app.route("/webhook/github-build", methods=["POST"])
    def github_build_webhook():
        """Handle GitHub Actions workflow_run events.

        Fires when a build completes. We verify the GitHub signature and
        send the owner a Telegram alert if the build failed.
        """
        github_secret = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()
        if github_secret:
            signature = request.headers.get("X-Hub-Signature-256", "")
            payload_raw = request.get_data(as_text=True)

            expected_sig = (
                "sha256="
                + hmac.new(
                    github_secret.encode(),
                    payload_raw.encode(),
                    hashlib.sha256,
                ).hexdigest()
            )

            if not hmac.compare_digest(signature, expected_sig):
                print("[GitHub Webhook] Signature verification failed")
                abort(403)

        try:
            payload = request.get_json()
            if not payload:
                return "OK", 200

            action = payload.get("action")
            if action != "completed":
                return "OK", 200

            run = payload.get("workflow_run", {})
            conclusion = run.get("conclusion")
            name = run.get("name", "Build")
            html_url = run.get("html_url")
            head_commit = payload.get("workflow_run", {}).get("head_commit", {})
            commit_msg = head_commit.get("message", "No message")

            # Only alert on failure.
            if conclusion == "failure":
                build_status = {
                    "status": "failure",
                    "conclusion": "failure",
                    "logs_url": html_url,
                    "created_at": run.get("created_at"),
                    "branch": run.get("head_branch"),
                    "commit": head_commit.get("id", "?")[:7],
                }

                # Pulling full logs from the run is limited by the GitHub API,
                # so for now we just analyze the commit message heuristically.
                analysis = analyze_build_logs(commit_msg)

                alert = format_build_alert(build_status, analysis)

                owner_id = os.getenv("OWNER_CHAT_ID")
                if owner_id and telegram_bot:
                    try:
                        telegram_bot.send_message(
                            owner_id,
                            alert,
                            parse_mode="Markdown",
                        )
                    except Exception as e:
                        print(f"[GitHub Webhook] Failed to send Telegram alert: {e}")

                if mongo_db:
                    try:
                        mongo_db.github_builds.insert_one(
                            {
                                "run_id": run.get("id"),
                                "name": name,
                                "conclusion": conclusion,
                                "html_url": html_url,
                                "commit_message": commit_msg,
                                "branch": run.get("head_branch"),
                                "created_at": run.get("created_at"),
                                "timestamp": datetime.now(timezone.utc),
                            }
                        )
                    except Exception as e:
                        print(f"[GitHub Webhook] Failed to log build: {e}")

                # M5: bucket failing builds by workflow name so a pipeline
                # that keeps breaking turns into a real tracked issue.
                try:
                    from app.agent import incident_tracker
                    incident_tracker.maybe_escalate(
                        source="github_actions",
                        category=str(name)[:120],
                        detail=(
                            f"branch={run.get('head_branch')}, "
                            f"commit={head_commit.get('id', '?')[:7]}, "
                            f"url={html_url}"
                        ),
                        evidence=commit_msg[:1500] if commit_msg else "",
                    )
                except Exception as e:
                    print(f"[GitHub Webhook] incident_tracker hook failed: {e}")

            return "OK", 200

        except Exception as e:
            print(f"[GitHub Webhook] Error: {e}")
            log_unhandled_exception(mongo_db, e, source="github_webhook")
            return "OK", 200  # 200 so GitHub doesn't retry/spam us

    # M14b: Sentry to Telegram webhook.
    #
    # One-time setup on the Sentry side:
    #   1. Settings → Developer Settings → "New Internal Integration"
    #   2. Webhook URL: https://<heroku-app>.herokuapp.com/webhook/sentry
    #   3. Permissions: Issue & Event → Read
    #   4. Webhooks: tick "issue" (created / resolved)
    #   5. Save → copy the "Client Secret"
    #   6. heroku config:set SENTRY_WEBHOOK_SECRET=<that secret>
    #
    # Sentry signs every POST body with HMAC-SHA256 of the raw body using the
    # integration's client secret; we verify before doing anything.

    @app.route("/webhook/sentry", methods=["POST"])
    def sentry_webhook():
        sentry_secret = os.getenv("SENTRY_WEBHOOK_SECRET", "").strip()
        signature = request.headers.get("Sentry-Hook-Signature", "")
        payload_raw = request.get_data(as_text=True)

        if not sentry_secret:
            # Without a secret we can't verify the signature, so refuse rather
            # than accept unsigned posts. Log it so the owner spots the misconfig.
            print(
                "[Sentry Webhook] SENTRY_WEBHOOK_SECRET not set, refusing"
            )
            abort(503)

        expected_sig = hmac.new(
            sentry_secret.encode(),
            payload_raw.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not (signature and hmac.compare_digest(signature, expected_sig)):
            print("[Sentry Webhook] Signature verification failed")
            abort(403)

        try:
            payload = request.get_json(silent=True) or {}
            data = payload.get("data") or {}
            issue = data.get("issue") or {}
            event = data.get("event") or {}

            # Only act on new or regressed issues. Don't ping Telegram for
            # `resolved` and friends, that's just noise.
            action = (payload.get("action") or "").strip()
            if action not in ("created", "triggered", ""):
                return "OK", 200

            title = (
                issue.get("title")
                or event.get("title")
                or payload.get("title")
                or "Sentry issue"
            )
            level = (
                issue.get("level")
                or event.get("level")
                or payload.get("level")
                or "error"
            )
            project = (
                issue.get("project", {}).get("slug")
                if isinstance(issue.get("project"), dict)
                else issue.get("project")
            ) or payload.get("project_slug") or ""
            culprit = (
                issue.get("culprit") or event.get("culprit") or ""
            )
            url = (
                issue.get("web_url")
                or issue.get("url")
                or payload.get("url")
                or ""
            )

            # Top of the stack trace, the most actionable line.
            stack_tail = ""
            try:
                frames = (
                    event.get("exception", {})
                    .get("values", [{}])[0]
                    .get("stacktrace", {})
                    .get("frames", [])
                )
                if frames:
                    # Sentry orders frames bottom-to-top; the last entry is
                    # the one that raised. Show the last 3.
                    pieces = []
                    for f in frames[-3:]:
                        loc = (
                            f.get('filename')
                            or f.get('abs_path')
                            or '?'
                        )
                        line = f.get('lineno', '?')
                        func = f.get('function') or '?'
                        pieces.append(f"  {loc}:{line} in {func}()")
                    stack_tail = "\n".join(pieces)
            except Exception:
                stack_tail = ""

            level_emoji = {
                "fatal": "🔥",
                "error": "🚨",
                "warning": "⚠️",
                "info": "ℹ️",
                "debug": "🐛",
            }.get(str(level).lower(), "🚨")

            lines = [
                f"{level_emoji} *Sentry — {level}*",
                f"*{title[:200]}*",
            ]
            if project:
                lines.append(f"project: `{project}`")
            if culprit:
                lines.append(f"culprit: `{culprit[:120]}`")
            if stack_tail:
                lines.append(f"```\n{stack_tail[:500]}\n```")
            if url:
                lines.append(f"[افتح على Sentry]({url})")

            owner_id = os.getenv("OWNER_CHAT_ID")
            if owner_id and telegram_bot:
                try:
                    telegram_bot.send_message(
                        owner_id,
                        "\n".join(lines),
                        parse_mode="Markdown",
                        disable_web_page_preview=True,
                    )
                except Exception as exc:
                    print(f"[Sentry Webhook] Telegram send failed: {exc}")

            # M5: record it, and open a tracking issue if this same title
            # keeps firing.
            try:
                from app.agent import incident_tracker
                incident_tracker.maybe_escalate(
                    source="sentry",
                    category=str(title)[:120],
                    detail=(
                        f"level={level}, project={project}, "
                        f"culprit={culprit or 'n/a'}"
                    ),
                    evidence=stack_tail or "",
                )
            except Exception as exc:
                print(f"[Sentry Webhook] incident_tracker hook failed: {exc}")

            return "OK", 200
        except Exception as exc:
            print(f"[Sentry Webhook] Error: {exc}")
            log_unhandled_exception(mongo_db, exc, source="sentry_webhook")
            # Always 200 so Sentry doesn't disable the integration over a
            # one-off parse failure.
            return "OK", 200

    # ملاحظة: Sandy ↔ ESP الآن عبر MQTT (HiveMQ Cloud) — لا تحتاج HTTP endpoints.
    # المنطق في app/integrations/sandy_device.py

    @app.route("/metrics", methods=["GET"])
    def metrics_endpoint():
        data, content_type = metrics.metrics_wsgi()
        return (data, 200, {
            "Content-Type": content_type,
            "Cache-Control": "no-store",
        })

    from app.api.voice_ws import register_voice_ws
    register_voice_ws(app)

    from app.api.productivity_api import register_productivity_api
    register_productivity_api(app, mongo_db=mongo_db)

    from app.api.emails_api import register_emails_api
    register_emails_api(app, mongo_db=mongo_db)

    from app.api.studio_api import register_studio_api
    register_studio_api(app, mongo_db=mongo_db)

    from app.api.life_api import register_life_api
    register_life_api(app, mongo_db=mongo_db)

    # Langfuse stats for the /status frontend page.
    @app.route("/api/langfuse-stats", methods=["GET"])
    def langfuse_stats():
        import requests as _req
        from flask import jsonify as _json

        pub  = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        sec  = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").strip()

        if not pub or not sec:
            return _json({"configured": False}), 200

        auth = (pub, sec)
        base = host.rstrip("/")

        try:
            # Daily metrics: token counts and cost.
            daily = _req.get(
                f"{base}/api/public/metrics/daily",
                params={"days": 1},
                auth=auth, timeout=6,
            )
            daily_data = daily.json() if daily.ok else {}

            # Recent traces: count and average latency.
            traces_r = _req.get(
                f"{base}/api/public/traces",
                params={"limit": 50, "page": 1},
                auth=auth, timeout=6,
            )
            traces_data = traces_r.json() if traces_r.ok else {}

        except Exception as exc:
            return _json({"configured": True, "error": str(exc)}), 200

        traces = traces_data.get("data", [])
        latencies = [
            t.get("latency") for t in traces if t.get("latency") is not None
        ]
        avg_latency = round(sum(latencies) / len(latencies)) if latencies else None

        # daily_data shape: {"data": [{"date":"...", "countTraces":N, "totalCost":F, ...}]}
        today = (daily_data.get("data") or [{}])[0]

        return _json({
            "configured": True,
            "traces_today":  today.get("countTraces", 0),
            "tokens_today":  today.get("totalTokens") or today.get("inputTokens", 0),
            "cost_today_usd": today.get("totalCost", 0),
            "avg_latency_ms": avg_latency,
            "total_traces":  traces_data.get("meta", {}).get("totalItems", len(traces)),
        }), 200

    # Register the Telegram callback handler for access approve/deny.
    if telegram_bot:

        def _apply_custom_boost(message, jti, chat_type):
            """Owner replied with a custom number of extra uses to grant."""
            from app.agent.guest_usage import approve_guest, guest_label
            raw = (getattr(message, "text", "") or "").strip()
            try:
                boost = int(raw)
            except (TypeError, ValueError):
                boost = 0
            if boost <= 0 or boost > 1000:
                telegram_bot.send_message(
                    message.chat.id, "❌ رقم غير صالح — أرسل عدداً بين 1 و1000"
                )
                return
            ok = approve_guest(jti, chat_type, boost, mongo_db)
            label = guest_label(jti)
            telegram_bot.send_message(
                message.chat.id,
                f"✅ تم منح {label} +{boost} {chat_type}" if ok else "❌ فشل التحديث",
            )

        @telegram_bot.callback_query_handler(
            func=lambda call: call.data.startswith("gapprove:")
            or call.data.startswith("greject:")
            or call.data.startswith("gcustom:")
        )
        def handle_guest_usage_callback(call):
            from app.agent.guest_usage import approve_guest, reject_guest, guest_label
            try:
                telegram_bot.edit_message_reply_markup(
                    call.message.chat.id, call.message.message_id, reply_markup=None
                )
            except Exception:
                pass
            parts = call.data.split(":")
            action = parts[0]
            if action == "gcustom" and len(parts) == 3:
                # Ask the owner to type the exact number, then capture the reply.
                _, jti, chat_type = parts
                prompt = telegram_bot.send_message(
                    call.message.chat.id,
                    f"✏️ كم استخدام إضافي تمنح {guest_label(jti)} لـ {chat_type}؟ "
                    f"أرسل رقماً (مثال: 7)",
                    reply_markup=telebot.types.ForceReply(selective=False),
                )
                telegram_bot.register_next_step_handler(
                    prompt, _apply_custom_boost, jti, chat_type
                )
                telegram_bot.answer_callback_query(call.id, "أرسل الرقم 👇")
                return
            if action == "gapprove" and len(parts) == 4:
                _, jti, chat_type, boost_str = parts
                boost = int(boost_str)
                ok = approve_guest(jti, chat_type, boost, mongo_db)
                label = guest_label(jti)
                msg = f"✅ تم منح {label} +{boost} {chat_type}" if ok else "❌ فشل التحديث"
            elif action == "greject" and len(parts) == 3:
                _, jti, chat_type = parts
                ok = reject_guest(jti, chat_type, mongo_db)
                label = guest_label(jti)
                msg = f"🚫 تم رفض {label} — {chat_type}" if ok else "❌ فشل التحديث"
            else:
                msg = "❌ بيانات غير صالحة"
            telegram_bot.answer_callback_query(call.id, msg[:200])
            telegram_bot.send_message(call.message.chat.id, msg)

        @telegram_bot.callback_query_handler(
            func=lambda call: call.data.startswith("access_approve:") or call.data.startswith("access_deny:")
        )
        def handle_access_callback(call):
            parts = call.data.split(":", 1)
            action, request_id = parts[0], parts[1]
            try:
                telegram_bot.edit_message_reply_markup(
                    call.message.chat.id, call.message.message_id, reply_markup=None
                )
            except Exception:
                pass
            if action == "access_approve":
                token = approve_access_request(request_id)
                msg = f"✅ تم الموافقة على طلب الوصول `{request_id}`" if token else "❌ الطلب غير موجود أو انتهت صلاحيته"
            else:
                ok = deny_access_request(request_id)
                msg = f"🚫 تم رفض طلب الوصول `{request_id}`" if ok else "❌ الطلب غير موجود"
            telegram_bot.answer_callback_query(call.id, msg[:200])
            telegram_bot.send_message(call.message.chat.id, msg)

        @telegram_bot.callback_query_handler(
            func=lambda call: call.data.startswith("remsnz:")
            or call.data.startswith("remdone:")
        )
        def handle_reminder_buttons(call):
            """Snooze/done buttons under a delivered reminder (owner only)."""
            from app.config import OWNER_CHAT_ID as _owner
            from app.utils.user_profiles import active_user_profile_context

            if str(call.message.chat.id) != str(_owner or ""):
                telegram_bot.answer_callback_query(call.id, "هذا خاص بنبيل 😊")
                return

            owner_profile = {
                "chat_id": str(_owner),
                "name": "",
                "relation": "owner",
                "tone": "casual",
                "permissions": "all",
            }
            parts = call.data.split(":")
            try:
                telegram_bot.edit_message_reply_markup(
                    call.message.chat.id, call.message.message_id, reply_markup=None
                )
            except Exception:
                pass

            if parts[0] == "remsnz" and len(parts) == 3:
                from app.features.reminders_store import snooze_reminder

                minutes = int(parts[2])
                with active_user_profile_context(owner_profile):
                    res = snooze_reminder(parts[1], minutes)
                if res.get("success"):
                    label = "بكرة" if minutes >= 1440 else f"{minutes} دقيقة"
                    msg = f"⏰ تمام، بذكرك بعد {label}"
                else:
                    msg = "ما قدرت أأجّل التذكير"
            elif parts[0] == "remdone" and len(parts) == 2:
                from app.features.reminders_store import complete_reminder

                with active_user_profile_context(owner_profile):
                    ok = complete_reminder(parts[1])
                msg = "✅ تمام، سكرت التذكير" if ok else "ما لقيت التذكير"
            else:
                msg = "زر غير صالح"
            telegram_bot.answer_callback_query(call.id, msg[:200])
            telegram_bot.send_message(call.message.chat.id, msg)

    # Auth endpoints
    @app.route("/api/auth", methods=["POST"])
    def web_auth():
        body = request.get_json(silent=True) or {}
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
        allowed, remaining = check_rate_limit(ip)
        if not allowed:
            return jsonify({"error": "too_many_attempts"}), 429
        password = (body.get("password") or "").strip()
        if not password or not check_owner_password(password):
            return jsonify({"error": "invalid_password", "remaining": remaining - 1}), 401
        try:
            token = make_token("owner")
        except RuntimeError:
            return jsonify({"error": "auth_not_configured"}), 503
        return jsonify({"token": token, "role": "owner"}), 200

    @app.route("/api/access/request", methods=["POST"])
    def web_access_request():
        from app.config import OWNER_CHAT_ID
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "زاير").strip()[:50]
        reason = (body.get("reason") or "").strip()[:200]
        request_id = store_access_request(name, reason)
        if telegram_bot and OWNER_CHAT_ID:
            try:
                import telebot as _telebot
                markup = _telebot.types.InlineKeyboardMarkup()
                markup.row(
                    _telebot.types.InlineKeyboardButton("✅ وافق", callback_data=f"access_approve:{request_id}"),
                    _telebot.types.InlineKeyboardButton("❌ ارفض", callback_data=f"access_deny:{request_id}"),
                )
                reason_line = f"\nالسبب: {reason}" if reason else ""
                telegram_bot.send_message(
                    OWNER_CHAT_ID,
                    f"🔔 طلب وصول جديد للموقع\nالاسم: {name}{reason_line}\nID: `{request_id}`",
                    reply_markup=markup,
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return jsonify({"request_id": request_id}), 200

    @app.route("/api/access/status/<request_id>", methods=["GET"])
    def web_access_status(request_id):
        data = get_access_request(request_id)
        if not data:
            return jsonify({"status": "expired"}), 404
        resp = {"status": data["status"]}
        if data["status"] == "approved":
            resp["token"] = data["token"]
            resp["role"] = "guest"
        return jsonify(resp), 200

    # Web chat history (MongoDB)
    def _chat_history_key(claims):
        from app.config import SANDY_USER_CHAT_ID
        if claims.get("role") == "owner":
            return f"web_chat_{SANDY_USER_CHAT_ID}"
        return f"web_chat_{claims.get('jti', 'guest')}"

    @app.route("/api/chat/history", methods=["GET"])
    @require_auth
    def get_chat_history(claims):
        if mongo_db is None:
            return jsonify({"messages": []}), 200
        key = _chat_history_key(claims)
        doc = mongo_db.web_chat_history.find_one({"_id": key}, {"_id": 0, "messages": 1})
        return jsonify({"messages": doc["messages"] if doc else []}), 200

    @app.route("/api/chat/history", methods=["PUT"])
    @require_auth
    def put_chat_history(claims):
        from datetime import timedelta
        if mongo_db is None:
            return jsonify({"ok": True}), 200
        body = request.get_json(silent=True) or {}
        messages = body.get("messages", [])
        key = _chat_history_key(claims)
        expire_at = None if claims.get("role") == "owner" else \
            datetime.now(timezone.utc) + timedelta(hours=48)
        doc = {"_id": key, "messages": messages, "updated_at": datetime.now(timezone.utc)}
        if expire_at:
            doc["expire_at"] = expire_at
        mongo_db.web_chat_history.replace_one({"_id": key}, doc, upsert=True)
        return jsonify({"ok": True}), 200

    # Web agent endpoint: full SA pipeline, shared memory.
    @app.route("/api/agent", methods=["POST"])
    @require_auth
    def web_agent(claims):
        from app.agent.graph.graph import run_graph, get_final_reply
        from app.config import SANDY_USER_CHAT_ID

        body = request.get_json(silent=True) or {}

        message = (body.get("message") or "").strip()
        if not message:
            return jsonify({"error": "no message"}), 400

        # Site language picks Sandy's reply language (same personality).
        lang = (body.get("lang") or "ar").strip().lower()

        role = claims.get("role", "guest")

        # Owner gets the full SA pipeline.
        if role == "owner":
            try:
                import base64
                from app.utils.user_profiles import (
                    active_user_profile_context, OWNER_CHAT_ID,
                )
                # The privileged handlers (reminders/tasks/calendar) refuse
                # with "خاصة بنبيل" unless an owner profile is active. The
                # Telegram path sets it via active_user_profile_context, so the
                # web path does the same to give the owner full access.
                _owner_profile = {
                    "chat_id": OWNER_CHAT_ID or str(SANDY_USER_CHAT_ID),
                    "name": "", "relation": "owner",
                    "tone": "casual", "permissions": "all",
                }
                graph_message = message
                if lang == "en":
                    graph_message = (
                        f"{message}\n\n(Note: I'm on the English interface — please "
                        "reply in English, keeping your usual personality.)"
                    )
                with active_user_profile_context(_owner_profile):
                    state = run_graph(
                        graph_message,
                        user_id=str(SANDY_USER_CHAT_ID),
                        chat_id=str(SANDY_USER_CHAT_ID),
                        source="web",
                    )
                reply = get_final_reply(state)
                chunks = reply.get("chunks") or [reply.get("text", "")]
                text = "\n".join(chunks)
                img_bytes = reply.get("image_bytes")
                if img_bytes:
                    b64 = base64.b64encode(img_bytes).decode()
                    caption = reply.get("caption") or text
                    return jsonify({
                        "reply": caption,
                        "image_url": f"data:image/png;base64,{b64}",
                        "role": "owner",
                    }), 200
                return jsonify({"reply": text, "role": "owner"}), 200
            except Exception as exc:
                logger.exception("[web_agent] owner pipeline failed")
                log_unhandled_exception(mongo_db, exc, source="web_agent")
                return jsonify({"error": "internal_error"}), 500

        # Guest path: rate-limit check, then a friendly basic chat.
        from app.agent.guest_usage import check_and_increment, guest_label
        jti = claims.get("jti", "")
        # One shared budget for guests across chat, search, voice and images.
        chat_type = "all"
        guest_name = claims.get("name") or (guest_label(jti) if jti else "زائر")
        status, count, limit = check_and_increment(jti, guest_name, chat_type, mongo_db)
        if status == "pending":
            return jsonify({
                "error": "limit_reached",
                "message": f"وصلت للحد المسموح ({limit} {chat_type}). طلبت الإذن من المسؤول — انتظر الموافقة.",
                "count": count, "limit": limit,
            }), 429
        if status == "block":
            return jsonify({
                "error": "access_denied",
                "message": "تم رفض طلبك من المسؤول.",
                "count": count, "limit": limit,
            }), 403

        try:
            from app.config import GUEST_PERSONALITY
            from app.agent.facade.agent import create_chat_completion
            history = body.get("history") or []
            guest_system = GUEST_PERSONALITY
            if lang == "en":
                guest_system += (
                    " IMPORTANT: The user is on the English interface — reply in natural, "
                    "fluent English while keeping the same warm, friendly personality "
                    "(you can still say you're Sandy, built by Nabeel)."
                )
            messages = [{"role": "system", "content": guest_system}]
            for h in history[-6:]:
                r = "user" if h.get("role") == "user" else "assistant"
                messages.append({"role": r, "content": h.get("text", "")})
            messages.append({"role": "user", "content": message})
            # Route through the shared chat-completion helper so guest chat gets
            # the same circuit breaker + timeouts as the rest of the pipeline.
            resp = create_chat_completion(messages=messages, max_tokens=300)
            return jsonify({"reply": resp.choices[0].message.content, "role": "guest"}), 200
        except Exception as exc:
            logger.exception("[web_agent] guest chat failed")
            log_unhandled_exception(mongo_db, exc, source="web_agent_guest")
            return jsonify({"error": "internal_error"}), 500

    @app.route("/api/image", methods=["POST"])
    @require_auth
    def web_image(claims):
        body = request.get_json(silent=True) or {}

        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"error": "no prompt"}), 400

        # Rate-limit guests on image generation
        if claims.get("role") != "owner":
            from app.agent.guest_usage import check_and_increment, guest_label
            jti = claims.get("jti", "")
            guest_name = claims.get("name") or (guest_label(jti) if jti else "زائر")
            status, count, limit = check_and_increment(jti, guest_name, "all", mongo_db)
            if status == "pending":
                return jsonify({
                    "error": "limit_reached",
                    "message": f"وصلت للحد المسموح ({limit}). طلبت الإذن من المسؤول — انتظر الموافقة.",
                    "count": count, "limit": limit,
                }), 429
            if status == "block":
                return jsonify({
                    "error": "access_denied",
                    "message": "تم رفض طلبك من المسؤول.",
                }), 403

        try:
            import base64
            from app.features.vision import generate_image_with_azure
            img_bytes = generate_image_with_azure(prompt)
            if img_bytes:
                b64 = base64.b64encode(img_bytes).decode()
                return jsonify({"url": f"data:image/png;base64,{b64}"}), 200
            return jsonify({"error": "تعذّر توليد الصورة"}), 500
        except Exception as exc:
            logger.exception("[web_image] image generation failed")
            log_unhandled_exception(mongo_db, exc, source="web_image")
            return jsonify({"error": "internal_error"}), 500

    @app.route("/api/guest-usage/status", methods=["GET"])
    @require_auth
    def guest_usage_status(claims):
        """Read-only poll so the web UI can tell a guest when the owner
        approved or rejected their pending request. Does NOT consume usage."""
        from app.agent.guest_usage import get_usage_doc
        if claims.get("role") == "owner":
            return jsonify({"state": "approved", "count": 0, "limit": 0}), 200
        # unified budget → always read the shared "all" doc, whatever type the UI polls
        jti = claims.get("jti", "")
        doc = get_usage_doc(jti, "all", mongo_db) or {}
        return jsonify({
            "state": doc.get("approval_state", "none"),
            "count": doc.get("count", 0),
            "limit": doc.get("limit", 3),
        }), 200

    @app.route("/api/analyze-image", methods=["POST"])
    @require_auth
    def web_analyze_image(claims):
        """Analyze an image (base64) via Azure Vision and return Sandy's description."""
        body = request.get_json(silent=True) or {}

        image_b64 = (body.get("image") or "").strip()
        question = (body.get("question") or "صف هذه الصورة بتفصيل").strip()
        if (body.get("lang") or "ar").strip().lower() == "en":
            question = f"{question}\n\n(Reply in English.)"
        if not image_b64:
            return jsonify({"error": "no image"}), 400

        # Rate-limit guests (shared unified budget)
        if claims.get("role") != "owner":
            from app.agent.guest_usage import check_and_increment, guest_label
            jti = claims.get("jti", "")
            guest_name = claims.get("name") or (guest_label(jti) if jti else "زائر")
            status, count, limit = check_and_increment(jti, guest_name, "all", mongo_db)
            if status == "pending":
                return jsonify({
                    "error": "limit_reached",
                    "message": f"وصلت للحد المسموح ({limit}). طُلب الإذن من المسؤول.",
                    "count": count, "limit": limit,
                }), 429
            if status == "block":
                return jsonify({"error": "access_denied", "message": "تم رفض طلبك."}), 403

        try:
            import base64 as _b64
            from app.features.vision import analyze_image_with_azure
            # analyze_image_with_azure needs the bound chat-completion fn (the
            # one with the Azure/OpenAI clients and circuit breaker) that the
            # Telegram pipeline uses. Without it the call raised TypeError and
            # every web image analysis failed.
            from app.agent.facade.agent import create_chat_completion
            img_bytes = _b64.b64decode(image_b64)
            reply = analyze_image_with_azure(
                img_bytes, question, create_chat_completion_fn=create_chat_completion
            )
            return jsonify({"reply": reply or "تعذّر تحليل الصورة"}), 200
        except Exception as exc:
            logger.exception("[web_analyze_image] image analysis failed")
            log_unhandled_exception(mongo_db, exc, source="web_analyze_image")
            return jsonify({"error": "internal_error"}), 500

    @app.route('/')
    def index():
        from flask import redirect
        frontend = os.getenv('FRONTEND_URL', '').rstrip('/')
        if frontend:
            return redirect(frontend)
        return jsonify({'status': 'Sandy API running'}), 200

    return app


