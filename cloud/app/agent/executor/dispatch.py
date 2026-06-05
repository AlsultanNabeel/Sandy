from typing import Any, Dict, Optional

from app.agent.executor.calendar_handlers import handle_calendar_action
from app.agent.executor.reminder_handlers import handle_reminder_action
from app.agent.executor.task_handlers import handle_task_action
import os
from datetime import datetime


def capture_room_panorama() -> Optional[bytes]:
    """يلتقط ٣ صور من الكاميرا (يسار/وسط/يمين) ويركّبها بانوراما side-by-side.

    التسلسل لكل زاوية: لف → استنى ٢.٥ ثانية ليستقر السيرفو تماماً → صوّر.
    قابل للاستدعاء من خارج dispatch (مثلاً من زر 'نسخة ثانية' في تلجرام).
    """
    import time as _time
    import io as _io
    try:
        from PIL import Image as _PIL
    except Exception:
        return None
    from app.integrations.sandy_device import get_sandy_device_client
    from app.integrations.esp32_camera import get_camera_client

    device = get_sandy_device_client()
    camera = get_camera_client()

    # الزوايا من اليسار لليمين عشان البانوراما تطلع منطقية. الـ sine easing على ESP
    # بياخد ~1.5-2s لحركة كبيرة (٦٠°+) فبدنا 2.5s قبل التصوير.
    angles = [150, 90, 30]
    shots = []
    for a in angles:
        try:
            device.set_servo(a)
        except Exception:
            pass
        _time.sleep(2.5)  # وقت كافٍ ليلف ويستقر — أهم من السرعة
        print(f"[room_scan] capturing at angle={a}")
        snap = camera.capture_snapshot(timeout_sec=15.0)
        if snap:
            shots.append(snap)
        else:
            print(f"[room_scan] no snapshot at angle={a}")
    try:
        device.set_servo(90)
    except Exception:
        pass

    if len(shots) < 2:
        return None
    try:
        imgs = [_PIL.open(_io.BytesIO(b)).convert("RGB") for b in shots]
        h = max(im.height for im in imgs)
        w = sum(im.width for im in imgs)
        pano = _PIL.new("RGB", (w, h), (0, 0, 0))
        x = 0
        for im in imgs:
            if im.height != h:
                im = im.resize((int(im.width * h / im.height), h))
            pano.paste(im, (x, 0))
            x += im.width
        out = _io.BytesIO()
        pano.save(out, format="JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return None


def execute_operational_action(
    action_type: str,
    params: Dict[str, Any],
    *,
    user_message: str,
    normalized_user_message: str,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    create_chat_completion_fn,
    save_session_fn,
) -> Dict[str, Any]:
    action_type = str(action_type or "").strip().lower()
    params = params or {}

    # Shadow draft: stash in session and ask for confirmation.
    if action_type == "shadow_draft":
        from app.agent.shadow_execution import handle_shadow_draft_action

        return handle_shadow_draft_action(params, session)

    if action_type == "task":
        return handle_task_action(
            params,
            user_message=user_message,
            normalized_user_message=normalized_user_message,
            session=session,
            session_file=session_file,
            mongo_db=mongo_db,
            tasks_file=tasks_file,
            create_chat_completion_fn=create_chat_completion_fn,
            save_session_fn=save_session_fn,
        )
    if action_type == "reminder":
        return handle_reminder_action(
            params,
            user_message=user_message,
            normalized_user_message=normalized_user_message,
            session=session,
            session_file=session_file,
            mongo_db=mongo_db,
            tasks_file=tasks_file,
            create_chat_completion_fn=create_chat_completion_fn,
            save_session_fn=save_session_fn,
        )
    if action_type == "calendar":
        return handle_calendar_action(
            params,
            user_message=user_message,
            normalized_user_message=normalized_user_message,
            session=session,
            session_file=session_file,
            mongo_db=mongo_db,
            tasks_file=tasks_file,
            create_chat_completion_fn=create_chat_completion_fn,
            save_session_fn=save_session_fn,
        )

    # Heroku: logs, dyno status, restart, hours.
    if action_type == "heroku":
        return _handle_heroku_action(params)

    # Cost report across providers.
    if action_type == "cost":
        return _handle_cost_action(params, session)

    # Web research.
    if action_type in {"research", "research.web", "research.places"}:
        try:
            from app.features.research import execute_web_research
            from app.integrations.exa_client import search_exa, get_exa_page_content

            query = str(params.get("query") or user_message or "").strip()
            research_type = str(
                params.get("research_type") or params.get("type") or "general"
            ).strip()
            requested_count = int(
                params.get("count") or params.get("requested_count") or 5
            )

            exa_api_key = os.getenv("EXA_API_KEY", "").strip()

            reply, items = execute_web_research(
                query=query,
                user_message=user_message,
                research_type=research_type,
                requested_count=requested_count,
                search_exa_fn=search_exa,
                get_exa_page_content_fn=get_exa_page_content,
                create_chat_completion_fn=create_chat_completion_fn,
                exa_api_key=exa_api_key,
                session=session,
            )

            return {"handled": True, "reply": reply or "", "items": items}
        except Exception as e:
            return {"handled": False, "reply": f"⚠️ خطأ في تنفيذ البحث: {e}"}

    # Email: read, send, reply.
    if action_type == "email":
        try:
            from app.features.gmail import (
                format_inbox_digest,
                gmail_preview_for_session,
                get_unread_emails,
                reply_to_email,
            )
            from app.agent.pending import create_pending_action as _make_pending
            from app.utils.google_oauth_errors import (
                user_message_for_google_auth_failure,
            )

            ep = params or {}
            email_action = str(ep.get("action", "read") or "read").strip().lower()

            if email_action == "unread_count":
                emails = get_unread_emails(max_results=50)
                count = len(emails) if emails else 0
                if count == 0:
                    return {"handled": True, "reply": "📭 ما عندك رسائل غير مقروءة."}
                return {"handled": True, "reply": f"📬 عندك {count} رسالة غير مقروءة."}

            if email_action == "read":
                _count = int(ep.get("count") or 0) or None
                max_results = _count if _count and _count <= 20 else 10
                emails = get_unread_emails(max_results=max_results)
                if not emails:
                    return {
                        "handled": True,
                        "reply": "📭 لا يوجد بريد غير مقروء حالياً.",
                    }
                if _count:
                    emails = emails[:_count]
                gmail_list = {
                    **gmail_preview_for_session(emails),
                    "fetched_at": datetime.now().isoformat(),
                }
                session["gmail_last_list"] = gmail_list
                reply = format_inbox_digest(emails, numbering_start=1)
                return {"handled": True, "reply": reply, "items": emails}

            if email_action == "send":
                to = str(ep.get("to") or "").strip()
                subject = str(ep.get("subject") or "").strip()
                body = str(ep.get("body") or "").strip()

                if ep.get("needs_followup_body"):
                    if to:
                        session["pending_action"] = _make_pending(
                            {
                                "type": "email",
                                "action": "await_body",
                                "to": to,
                                "subject": subject,
                                "confirmation_status": "clarification",
                            }
                        )
                        return {
                            "handled": True,
                            "reply": f"✉️ تمام؛ المستلم: {to}\nاكتب نص الرسالة الآن.",
                        }
                    return {"handled": True, "reply": "حدّد عنوان المُستَلَم أولًا."}

                if not to:
                    return {"handled": True, "reply": "حدّد المستلم أولاً."}

                # Keep it in pending_action (not email_send_draft) so it survives.
                session["pending_action"] = _make_pending(
                    {
                        "type": "email",
                        "action": "confirm_send",
                        "to": to,
                        "subject": subject,
                        "body": body,
                        "confirmation_status": "pending",
                    }
                )
                from app.agent.email_resolve import _render_email_preview

                preview = _render_email_preview(to, subject, body)
                try:
                    import telebot.types as _tg

                    markup = _tg.InlineKeyboardMarkup()
                    markup.row(
                        _tg.InlineKeyboardButton("📨 ارسل", callback_data="email_send"),
                        _tg.InlineKeyboardButton(
                            "💾 مسودة", callback_data="email_draft"
                        ),
                    )
                    markup.row(
                        _tg.InlineKeyboardButton(
                            "✏️ تعديل", callback_data="email_edit"
                        ),
                        _tg.InlineKeyboardButton(
                            "❌ الغاء", callback_data="email_cancel"
                        ),
                    )
                    return {"handled": True, "reply": preview, "reply_markup": markup}
                except Exception:
                    return {"handled": True, "reply": preview}

            if email_action == "reply":
                email_id = str(ep.get("email_id") or "").strip()
                body = str(ep.get("body") or "").strip()
                if email_id:
                    reply_to_email(email_id, body)
                    return {"handled": True, "reply": "✅ تم إرسال الرد."}
                if session.get("gmail_last_list", {}).get("messages"):
                    return {
                        "handled": True,
                        "reply": "لم أجد الرسالة؛ اعرِض الإيميلات أولًا ثم جرّب «رد على آخر واحد» مع نص الرد.",
                    }
                return {
                    "handled": True,
                    "reply": "اعرض الوارد أولًا أو حدّد رقم الرسالة.",
                }

            return {"handled": False, "reply": "أمر بريد غير مدعوم."}
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
                return {"handled": True, "reply": f"⚠️ خطأ في البريد: {e}"}

    # Morning briefing.
    if action_type == "briefing":
        try:
            from app.agent.facade.briefing import build_morning_briefing

            memory = {
                "sandy_state": {
                    "home_city": session.get("home_city")
                    or session.get("sandy_state", {}).get("home_city")
                    or "October City",
                    "last_briefing_date": session.get("last_briefing_date")
                    or session.get("sandy_state", {}).get("last_briefing_date")
                    or "",
                }
            }
            reply = build_morning_briefing(
                memory=memory, mongo_db=mongo_db, tasks_file=tasks_file
            )
            session["last_briefing_date"] = datetime.now().strftime("%Y-%m-%d")
            session.setdefault("sandy_state", {})["last_briefing_date"] = session[
                "last_briefing_date"
            ]
            return {"handled": True, "reply": reply}
        except Exception as e:
            return {"handled": True, "reply": f"⚠️ خطأ في briefing: {e}"}

    # Hardware: talks to the device over HTTP long polling, plus the ESP32 camera.
    if action_type == "hardware":
        try:
            from app.integrations.sandy_device import get_sandy_device_client
            from app.integrations.esp32_camera import get_camera_client

            cmd = str(params.get("command") or "").strip().lower()
            val = params.get("value")
            _device = get_sandy_device_client()
            _camera = get_camera_client()

            if cmd == "face_change":
                _device.sync_mood_async(str(val))
                return {"handled": True, "reply": "حسناً — غيّرت التعبير."}
            if cmd == "servo_move":
                try:
                    _device.set_servo(int(val))
                except Exception:
                    pass
                return {"handled": True, "reply": "تم تحريك السيرفو."}
            if cmd == "buzzer":
                _device.play_buzzer(str(val))
                return {"handled": True, "reply": "تشغيل البازر."}
            if cmd == "move":
                _device.move_base(str(val))
                return {"handled": True, "reply": "تمام — بحركة القاعدة."}
            if cmd == "snapshot":
                # لو المستخدم حدد زاوية، لف السيرفو قبل التصوير
                angle = params.get("angle")
                if angle is not None:
                    try:
                        import time as _time
                        _device.set_servo(int(angle))
                        _time.sleep(0.8)  # وقت للسيرفو يستقر
                    except Exception:
                        pass
                snap = _camera.capture_snapshot(timeout_sec=20.0)
                if not snap:
                    return {"handled": True, "reply": "ما قدرت ألتقط الصورة — ESP-CAM ما رد. تأكد أنه شغّال."}

                mode = (params.get("mode") or "image").strip().lower()
                if mode not in ("image", "describe", "both"):
                    mode = "image"

                # image: صورة فقط بدون تحليل
                if mode == "image":
                    return {
                        "handled": True,
                        "reply": "",
                        "image_bytes": snap,
                        "image_source": "camera_snapshot",
                    }

                # describe / both: حلل الصورة
                try:
                    from app.features.vision import analyze_image_with_azure
                    user_prompt = str(params.get("prompt") or "صفي اللي بتشوفيه بإيجاز").strip()
                    description = analyze_image_with_azure(
                        snap, user_prompt,
                        create_chat_completion_fn=create_chat_completion_fn,
                    )
                except Exception as e:
                    description = f"[think] التقطت الصورة لكن صار خلل بالتحليل: {e}"

                # describe: نص فقط
                if mode == "describe":
                    return {"handled": True, "reply": description}

                # both: صورة + وصف
                return {
                    "handled": True,
                    "reply": description,
                    "image_bytes": snap,
                    "image_source": "camera_snapshot",
                }
            if cmd == "distance":
                d = _device.get_distance()
                # HC-SR04 المدى الفعلي 2-400cm — أي قيمة دون 2 = الحساس مفصول/مقطوع
                if d is None or d < 2:
                    return {"handled": True, "reply": "ما قدرت أقرأ المسافة — الحساس مش راجع قيمة (السلك ممكن مقطوع)."}
                return {"handled": True, "reply": f"المسافة الحالية: {d:.1f} سم."}
            if cmd == "room_scan":
                # الفعلي ع capture_room_panorama: لف → استنى 2.5s → صوّر، بشكل متسلسل.
                pano = capture_room_panorama()
                if not pano:
                    return {"handled": True, "reply": "ما قدرت أمسح الغرفة — الكاميرا ما ردت بصور كافية."}
                return {
                    "handled": True,
                    "reply": "",
                    "image_bytes": pano,
                    "image_source": "camera_room_scan",
                }
            if cmd == "track_object":
                from app.features.vision import analyze_image_with_azure
                target = str(params.get("target") or "الشخص").strip()
                snap = _camera.capture_snapshot(timeout_sec=15.0)
                if not snap:
                    return {"handled": True, "reply": "ما قدرت ألتقط صورة للتتبّع."}
                prompt = (
                    f"حدد موقع {target} في الصورة. ارجع كلمة واحدة فقط: "
                    "left | center | right | not_found"
                )
                position = analyze_image_with_azure(
                    snap, prompt,
                    create_chat_completion_fn=create_chat_completion_fn,
                ).strip().lower()
                # حرك السيرفو حسب الموقع — الزوايا الأكبر = يسار، الأصغر = يمين
                current_status = _device.get_full_status() or {}
                current_angle = 90  # افتراضي
                try:
                    current_angle = int(current_status.get("servo_angle", 90))
                except Exception:
                    pass
                new_angle = current_angle
                if "left" in position:
                    new_angle = min(175, current_angle + 25)
                elif "right" in position:
                    new_angle = max(5, current_angle - 25)
                if new_angle != current_angle:
                    _device.set_servo(new_angle)
                    return {"handled": True, "reply": f"شفت {target} من جهة {position} — حركت راسي للزاوية {new_angle}°."}
                if "not_found" in position:
                    return {"handled": True, "reply": f"ما لقيت {target} في الصورة."}
                return {"handled": True, "reply": f"{target} موجود في المنتصف — راسي مظبوط."}
            if cmd == "camera_power":
                on = str(val).lower() in {"on", "true", "1", "wake"}
                _camera.set_power(on)
                return {"handled": True, "reply": "تم ضبط طاقة الكاميرا."}

            return {"handled": False, "reply": "أمر أجهزة غير معروف."}
        except Exception as e:
            return {"handled": True, "reply": f"⚠️ خطأ في الهاردوير: {e}"}

    # Update the saved home city.
    if action_type == "update_location":
        city = str(params.get("city") or "").strip()
        if not city:
            return {"handled": False, "reply": "ادخل المدينة الجديدة."}
        session["home_city"] = city
        session.setdefault("sandy_state", {})["home_city"] = city
        return {"handled": True, "reply": f"تمام، خزّنت موقعك الجديد: {city}"}

    # Places search.
    if action_type == "places":
        try:
            from app.agent.deep_context import (
                persist_last_search_results,
                places_to_search_items,
                record_last_action,
            )
            from app.features.google_places import (
                format_places_for_reply,
                search_places,
            )

            query = str(params.get("query") or user_message or "").strip()
            if not query:
                return {"handled": False, "reply": "حدّد اسم المكان أو نوعه."}

            places_api_key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
            places = search_places(query, places_api_key, max_results=8)
            if places:
                persist_last_search_results(
                    session,
                    domain="places",
                    query=str(query or "")[:320],
                    items=places_to_search_items(places, limit=12),
                )
                record_last_action(
                    session,
                    "places_shown",
                    summary=str(query or "")[:200],
                    refs={"query": query},
                )
                reply = format_places_for_reply(places)
                return {"handled": True, "reply": reply, "items": places}
            return {"handled": True, "reply": f"ما لقيت أماكن تطابق '{query}'."}
        except Exception as e:
            return {"handled": True, "reply": f"⚠️ خطأ في البحث عن الأماكن: {e}"}

    # Image generation, via image_agent and the Replicate generator.
    if action_type == "image":
        try:
            from app.features.image_agent import handle_image_message
            from app.features.vision import generate_image_with_azure

            img_res = handle_image_message(
                user_message=user_message,
                session=session,
                create_chat_completion_fn=create_chat_completion_fn,
                generate_image_with_azure_fn=generate_image_with_azure,
                azure_openai_client=None,
                azure_openai_image_deployment=None,
            )

            if not img_res.get("handled"):
                return {"handled": False, "reply": "لم تُنفّذ عملية صورة."}
            reply = img_res.get("reply_text") or img_res.get("caption") or ""
            out = {"handled": True, "reply": reply}
            if img_res.get("image_bytes"):
                out["image_bytes"] = img_res.get("image_bytes")
                out["caption"] = img_res.get("caption")
            return out
        except Exception as e:
            return {"handled": True, "reply": f"⚠️ خطأ في معالجة الصورة: {e}"}

    # Image edit, on the active_image_bytes kept in the session.
    if action_type == "image_edit":
        try:
            from app.features.vision import edit_image_with_azure

            image_state = (session or {}).get("image_state") or {}
            image_bytes = image_state.get("active_image_bytes")
            if not image_bytes:
                return {"handled": True, "reply": "ما عندي صورة سابقة أعدّلها. ابعت صورة أولاً."}

            prompt = str(params.get("prompt") or user_message or "").strip()
            if not prompt:
                return {"handled": True, "reply": "شو التعديل اللي بدك ياه؟"}

            edit_prompt = (
                f"Edit this image as requested: {prompt}. "
                f"Preserve all people, faces, expressions, clothing, and background details exactly. "
                f"Only apply the specific change the user asked for."
            )
            edited_bytes = edit_image_with_azure(image_bytes, edit_prompt)
            if not edited_bytes:
                return {"handled": True, "reply": "ما قدرت أعدّل الصورة حالياً. جرّب مرة ثانية."}

            image_state["active_image_bytes"] = edited_bytes
            image_state.setdefault("active_image", {})["action"] = "edited"
            return {"handled": True, "reply": "تفضلي، عدّلت الصورة.", "image_bytes": edited_bytes}
        except Exception as e:
            return {"handled": True, "reply": f"⚠️ خطأ في تعديل الصورة: {e}"}

    # Current time and date.
    if action_type in {"time", "current_time", "get_time", "datetime"}:
        from app.utils.time import USER_TZ

        now = datetime.now(USER_TZ)
        day_names = {
            0: "الاثنين",
            1: "الثلاثاء",
            2: "الأربعاء",
            3: "الخميس",
            4: "الجمعة",
            5: "السبت",
            6: "الأحد",
        }
        day_ar = day_names.get(now.weekday(), "")
        time_str = now.strftime("%I:%M %p").replace("AM", "ص").replace("PM", "م")
        date_str = now.strftime("%d/%m/%Y")
        reply = f"🕐 الوقت الحالي: {time_str}\n📅 التاريخ: {date_str} ({day_ar})"
        return {"handled": True, "reply": reply}

    # Weather.
    if action_type == "weather":
        try:
            from app.features.weather import get_weather, format_weather_for_prompt

            city = str(params.get("city") or user_message or "").strip()
            if not city:
                return {"handled": False, "reply": "ادخل مدينة أو اسم مكان للطقس."}
            data = get_weather(city)
            if not data:
                return {
                    "handled": True,
                    "reply": f"ما قدرت أجيب بيانات الطقس لـ {city} حالياً.",
                }
            reply = format_weather_for_prompt(data)
            return {"handled": True, "reply": reply}
        except Exception as e:
            return {"handled": True, "reply": f"⚠️ خطأ في الحصول على الطقس: {e}"}

    return {"handled": False, "reply": ""}


def _handle_heroku_action(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle heroku action: logs | status | restart | hours | diagnose."""
    from app.tools.heroku_tool import (
        get_logs,
        diagnose_logs,
        get_dyno_status,
        restart_dyno,
        get_dyno_hours_used,
        format_heroku_report,
    )

    action = str(params.get("action", "logs") or "logs").strip().lower()
    try:
        if action == "logs":
            lines = int(params.get("lines", 100))
            log_text = get_logs(lines=lines)
            issues = diagnose_logs(log_text)
            reply = format_heroku_report(logs=log_text, issues=issues)

        elif action == "status":
            status = get_dyno_status()
            hours = get_dyno_hours_used()
            reply = format_heroku_report(dyno_status=status, hours=hours or None)

        elif action == "restart":
            dyno = str(params.get("dyno", "") or "").strip() or None
            reply = restart_dyno(dyno_name=dyno)

        elif action == "hours":
            hours = get_dyno_hours_used()
            if hours:
                reply = format_heroku_report(hours=hours)
            else:
                reply = "⚠️ بيانات الـ Dyno hours غير متاحة حالياً."

        elif action == "diagnose":
            log_text = get_logs(lines=200)
            issues = diagnose_logs(log_text)
            reply = format_heroku_report(logs=log_text, issues=issues)

        else:
            return {"handled": False, "reply": f"أمر heroku غير معروف: {action}"}

        return {"handled": True, "reply": reply}

    except EnvironmentError as e:
        return {"handled": True, "reply": f"⚠️ {e}"}
    except Exception as e:
        return {"handled": True, "reply": f"⚠️ خطأ في Heroku API: {e}"}


def _handle_cost_action(
    params: Dict[str, Any], session: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle cost action: all | azure | aws | heroku | mongodb | anthropic."""
    from app.tools.cost_tool import (
        get_all_costs,
        get_azure_cost,
        get_aws_cost,
        get_heroku_cost,
        format_cost_report,
    )

    provider = str(params.get("provider", "all") or "all").strip().lower()

    try:
        if provider == "azure":
            costs = [get_azure_cost()]
        elif provider == "aws":
            costs = [get_aws_cost()]
        elif provider == "heroku":
            costs = [get_heroku_cost()]
        else:
            costs = get_all_costs()

        reply = format_cost_report(costs)
        return {"handled": True, "reply": reply}

    except Exception as e:
        return {"handled": True, "reply": f"⚠️ خطأ في جلب بيانات الاستهلاك: {e}"}
