"""Web API for the "حياتي" tab: shopping, habits, expenses, journal, reading.

Same owner/guest pattern as productivity_api — guests get demo payloads,
owner gets the real stores inside the owner profile context.
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

_DEMO = {
    "shopping": [
        {"id": "d1", "text": "حليب", "done": False, "category": "بقالة", "price": 8, "qty": 2, "unit": "علبة"},
        {"id": "d2", "text": "تفاح", "done": False, "category": "خضار وفواكه", "price": 0, "qty": 1, "unit": ""},
        {"id": "d3", "text": "قهوة", "done": True, "category": "بقالة", "price": 25, "qty": 1, "unit": ""},
    ],
    "habits": [
        {"id": "d1", "name": "رياضة الصبح", "streak": 5, "done_today": True},
        {"id": "d2", "name": "قراءة نص ساعة", "streak": 12, "done_today": False},
    ],
    "expenses": {
        "items": [
            {"id": "d1", "amount": 25, "note": "غدا", "category": "أكل", "at": "2026-06-11T13:00:00"},
            {"id": "d2", "amount": 60, "note": "بنزين", "category": "مواصلات", "at": "2026-06-10T09:00:00"},
        ],
        "summary": {"total": 85, "count": 2, "by_category": {"مواصلات": 60, "أكل": 25}},
    },
    "journal": [
        {"id": "d1", "date": "2026-06-11", "text": "رحت عالطبيب وكان كل شي تمام"},
        {"id": "d2", "date": "2026-06-10", "text": "خلصت مرحلة مهمة بالمشروع"},
    ],
    "books": [
        {"id": "d1", "title": "العادات الذرية", "status": "reading", "total_pages": 320, "current_page": 145, "cover_url": ""},
        {"id": "d2", "title": "الخيميائي", "status": "done", "total_pages": 198, "current_page": 198, "cover_url": ""},
        {"id": "d3", "title": "قوة التركيز", "status": "wishlist", "total_pages": 0, "current_page": 0, "cover_url": ""},
    ],
    "scenes": [
        {"name": "study", "label": "دراسة", "icon": "📚", "builtin": True,
         "actions": [{"device": "light", "value": "85"}, {"device": "music", "value": "off"}]},
        {"name": "relax", "label": "راحة", "icon": "🌙", "builtin": True,
         "actions": [{"device": "light", "value": "35"}, {"device": "music", "value": "on"}]},
        {"name": "movie", "label": "فيلم", "icon": "🎬", "builtin": True,
         "actions": [{"device": "light", "value": "10"}, {"device": "curtain", "value": "close"}]},
    ],
}


def _owner(claims) -> bool:
    return claims.get("role") == "owner"


def register_life_api(app, mongo_db=None):
    # ── التسوق ──────────────────────────────────────────────────────────
    @app.route("/api/life/shopping", methods=["GET"])
    @require_auth
    def api_shopping_list(claims):
        if not _owner(claims):
            return jsonify({"items": _DEMO["shopping"], "demo": True}), 200
        from app.features.shopping_store import list_items

        with active_user_profile_context(_OWNER_PROFILE):
            items = list_items(include_bought=True)
        return jsonify({"items": items, "demo": False}), 200

    @app.route("/api/life/shopping", methods=["POST"])
    @require_owner
    def api_shopping_add(claims):
        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        if not text:
            return jsonify({"error": "text_required"}), 400
        from app.features.shopping_store import add_item

        with active_user_profile_context(_OWNER_PROFILE):
            ok = add_item(text, category=(body.get("category") or "").strip())
        return jsonify({"ok": ok}), 200

    @app.route("/api/life/shopping/<item_id>", methods=["PATCH"])
    @require_owner
    def api_shopping_check(item_id, claims):
        body = request.get_json(silent=True) or {}
        from app.features.shopping_store import check_item_by_id

        with active_user_profile_context(_OWNER_PROFILE):
            r = check_item_by_id(item_id, price=body.get("price"), qty=body.get("qty"))
        return jsonify(r), (200 if r.get("ok") else 404)

    @app.route("/api/life/shopping/<item_id>", methods=["DELETE"])
    @require_owner
    def api_shopping_delete(item_id, claims):
        from app.features.shopping_store import delete_item_by_id

        with active_user_profile_context(_OWNER_PROFILE):
            ok = delete_item_by_id(item_id)
        return jsonify({"ok": ok}), (200 if ok else 404)

    @app.route("/api/life/shopping/<item_id>/price", methods=["POST"])
    @require_owner
    def api_shopping_set_price(item_id, claims):
        body = request.get_json(silent=True) or {}
        from app.features.shopping_store import set_item_purchase

        with active_user_profile_context(_OWNER_PROFILE):
            ok = set_item_purchase(
                item_id,
                price=body.get("price"),
                qty=body.get("qty"),
                unit=body.get("unit"),
            )
        return jsonify({"ok": ok}), (200 if ok else 404)

    @app.route("/api/life/shopping/last-price", methods=["GET"])
    @require_owner
    def api_shopping_last_price(claims):
        text = (request.args.get("text") or "").strip()
        from app.features.shopping_store import last_price_for

        with active_user_profile_context(_OWNER_PROFILE):
            price = last_price_for(text)
        return jsonify({"price": price}), 200

    # ── العادات ─────────────────────────────────────────────────────────
    @app.route("/api/life/habits", methods=["GET"])
    @require_auth
    def api_habits_list(claims):
        if not _owner(claims):
            return jsonify({"items": _DEMO["habits"], "demo": True}), 200
        from app.features.habits_store import list_habits

        with active_user_profile_context(_OWNER_PROFILE):
            items = list_habits()
        return jsonify({"items": items, "demo": False}), 200

    @app.route("/api/life/habits", methods=["POST"])
    @require_owner
    def api_habits_add(claims):
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name_required"}), 400
        from app.features.habits_store import add_habit

        with active_user_profile_context(_OWNER_PROFILE):
            ok = add_habit(name)
        return jsonify({"ok": ok}), 200

    @app.route("/api/life/habits/checkin", methods=["POST"])
    @require_owner
    def api_habits_checkin(claims):
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        from app.features.habits_store import checkin

        with active_user_profile_context(_OWNER_PROFILE):
            r = checkin(name)
        return jsonify(r), (200 if r.get("ok") else 404)

    @app.route("/api/life/habits/uncheckin", methods=["POST"])
    @require_owner
    def api_habits_uncheckin(claims):
        body = request.get_json(silent=True) or {}
        habit_id = (body.get("id") or "").strip()
        from app.features.habits_store import uncheckin

        with active_user_profile_context(_OWNER_PROFILE):
            r = uncheckin(habit_id)
        return jsonify(r), (200 if r.get("ok") else 404)

    @app.route("/api/life/habits/detail", methods=["GET"])
    @require_owner
    def api_habits_detail(claims):
        habit_id = (request.args.get("id") or "").strip()
        from app.features.habits_store import habit_history

        with active_user_profile_context(_OWNER_PROFILE):
            r = habit_history(habit_id)
        return jsonify(r), (200 if r.get("ok") else 404)

    @app.route("/api/life/habits/<habit_id>", methods=["PATCH"])
    @require_owner
    def api_habits_rename(habit_id, claims):
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        from app.features.habits_store import rename_habit

        with active_user_profile_context(_OWNER_PROFILE):
            ok = rename_habit(habit_id, name)
        return jsonify({"ok": ok}), (200 if ok else 400)

    @app.route("/api/life/habits/<habit_id>", methods=["DELETE"])
    @require_owner
    def api_habits_delete(habit_id, claims):
        from app.features.habits_store import delete_habit

        with active_user_profile_context(_OWNER_PROFILE):
            ok = delete_habit(habit_id)
        return jsonify({"ok": ok}), (200 if ok else 404)

    # ── المصاريف ────────────────────────────────────────────────────────
    @app.route("/api/life/expenses", methods=["GET"])
    @require_auth
    def api_expenses(claims):
        if not _owner(claims):
            return jsonify({**_DEMO["expenses"], "demo": True}), 200
        from app.features.expenses_store import list_expenses, month_summary

        days = int(request.args.get("days", 30) or 30)
        with active_user_profile_context(_OWNER_PROFILE):
            items = list_expenses(days=days, limit=50)
            summary = month_summary(days=days)
        return jsonify({"items": items, "summary": summary, "demo": False}), 200

    @app.route("/api/life/expenses", methods=["POST"])
    @require_owner
    def api_expenses_add(claims):
        body = request.get_json(silent=True) or {}
        from app.features.expenses_store import add_expense

        with active_user_profile_context(_OWNER_PROFILE):
            ok = add_expense(
                body.get("amount", 0),
                note=(body.get("note") or "").strip(),
                category=(body.get("category") or "").strip(),
            )
        return jsonify({"ok": ok}), (200 if ok else 400)

    @app.route("/api/life/expenses/<expense_id>", methods=["DELETE"])
    @require_owner
    def api_expenses_delete(expense_id, claims):
        from app.features.expenses_store import delete_expense

        with active_user_profile_context(_OWNER_PROFILE):
            ok = delete_expense(expense_id)
        return jsonify({"ok": ok}), (200 if ok else 404)

    # ── اليوميات ────────────────────────────────────────────────────────
    @app.route("/api/life/journal", methods=["GET"])
    @require_auth
    def api_journal(claims):
        if not _owner(claims):
            return jsonify({"items": _DEMO["journal"], "demo": True}), 200
        from app.features.journal_store import recent_entries, search_entries

        q = (request.args.get("q") or "").strip()
        with active_user_profile_context(_OWNER_PROFILE):
            items = search_entries(q) if q else recent_entries(limit=30)
        return jsonify({"items": items, "demo": False}), 200

    @app.route("/api/life/journal", methods=["POST"])
    @require_owner
    def api_journal_add(claims):
        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        if not text:
            return jsonify({"error": "text_required"}), 400
        from app.features.journal_store import add_entry

        with active_user_profile_context(_OWNER_PROFILE):
            ok = add_entry(text)
        return jsonify({"ok": ok}), 200

    @app.route("/api/life/journal/<entry_id>", methods=["DELETE"])
    @require_owner
    def api_journal_delete(entry_id, claims):
        from app.features.journal_store import delete_entry

        with active_user_profile_context(_OWNER_PROFILE):
            ok = delete_entry(entry_id)
        return jsonify({"ok": ok}), (200 if ok else 404)

    # ── القراءة ─────────────────────────────────────────────────────────
    @app.route("/api/life/books", methods=["GET"])
    @require_auth
    def api_books(claims):
        if not _owner(claims):
            return jsonify({"items": _DEMO["books"], "demo": True, "stats": {"sessions": 4, "pages": 96, "minutes": 210}}), 200
        from app.features.reading_store import goal_progress, list_books, reading_stats

        with active_user_profile_context(_OWNER_PROFILE):
            items = list_books()
            stats = reading_stats(days=30)
            goal = goal_progress()
        return jsonify({"items": items, "stats": stats, "goal": goal, "demo": False}), 200

    @app.route("/api/life/books", methods=["POST"])
    @require_owner
    def api_books_add(claims):
        body = request.get_json(silent=True) or {}
        from app.features.reading_store import add_book

        with active_user_profile_context(_OWNER_PROFILE):
            r = add_book(
                (body.get("title") or "").strip(),
                status=(body.get("status") or "reading").strip(),
                total_pages=int(body.get("total_pages", 0) or 0),
                cover_url=(body.get("cover_url") or "").strip(),
                current_page=int(body.get("current_page", 0) or 0),
                author=(body.get("author") or "").strip(),
                category=(body.get("category") or "").strip(),
                fmt=(body.get("fmt") or "").strip(),
            )
        return jsonify(r), (200 if r.get("ok") else 400)

    @app.route("/api/life/books/status", methods=["POST"])
    @require_owner
    def api_books_status(claims):
        body = request.get_json(silent=True) or {}
        from app.features.reading_store import set_book_status

        with active_user_profile_context(_OWNER_PROFILE):
            r = set_book_status(
                (body.get("title") or "").strip(), (body.get("status") or "").strip()
            )
        return jsonify(r), (200 if r.get("ok") else 404)

    @app.route("/api/life/books/detail", methods=["GET"])
    @require_owner
    def api_book_detail(claims):
        from app.features.reading_store import get_book

        with active_user_profile_context(_OWNER_PROFILE):
            b = get_book((request.args.get("title") or "").strip())
        return jsonify(b or {"error": "not_found"}), (200 if b else 404)

    @app.route("/api/life/books/meta", methods=["POST"])
    @require_owner
    def api_book_meta(claims):
        body = request.get_json(silent=True) or {}
        from app.features.reading_store import set_book_meta

        def _opt(k, cast=str):
            v = body.get(k)
            return cast(v) if v is not None and str(v) != "" else None

        with active_user_profile_context(_OWNER_PROFILE):
            r = set_book_meta(
                (body.get("title") or "").strip(),
                author=_opt("author"), category=_opt("category"),
                rating=_opt("rating", int), fmt=_opt("fmt"),
                total_pages=_opt("total_pages", int), current_page=_opt("current_page", int),
            )
        return jsonify(r), (200 if r.get("ok") else 404)

    @app.route("/api/life/books/note", methods=["POST"])
    @require_owner
    def api_book_note(claims):
        body = request.get_json(silent=True) or {}
        from app.features.reading_store import add_note

        with active_user_profile_context(_OWNER_PROFILE):
            r = add_note((body.get("title") or "").strip(), (body.get("text") or "").strip())
        return jsonify(r), (200 if r.get("ok") else 400)

    @app.route("/api/life/books/quote", methods=["POST"])
    @require_owner
    def api_book_quote(claims):
        body = request.get_json(silent=True) or {}
        from app.features.reading_store import add_quote

        with active_user_profile_context(_OWNER_PROFILE):
            r = add_quote((body.get("title") or "").strip(), (body.get("text") or "").strip(),
                          page=int(body.get("page", 0) or 0))
        return jsonify(r), (200 if r.get("ok") else 400)

    @app.route("/api/life/books/goal", methods=["POST"])
    @require_owner
    def api_book_goal(claims):
        body = request.get_json(silent=True) or {}
        from app.features.reading_store import set_reading_goal

        with active_user_profile_context(_OWNER_PROFILE):
            r = set_reading_goal(
                books_year=int(body.get("books_year", 0) or 0),
                pages_year=int(body.get("pages_year", 0) or 0),
            )
        return jsonify(r), (200 if r.get("ok") else 400)

    # ── مشاهد الغرفة + التركيز ───────────────────────────────────────────
    @app.route("/api/life/scenes", methods=["GET"])
    @require_auth
    def api_scenes(claims):
        if not _owner(claims):
            return jsonify({"items": _DEMO["scenes"], "demo": True}), 200
        from app.features.scene_store import list_scenes

        with active_user_profile_context(_OWNER_PROFILE):
            items = list_scenes()
        return jsonify({"items": items, "demo": False}), 200

    @app.route("/api/life/scenes", methods=["POST"])
    @require_owner
    def api_scene_add(claims):
        body = request.get_json(silent=True) or {}
        from app.features.scene_store import add_scene

        with active_user_profile_context(_OWNER_PROFILE):
            r = add_scene(
                (body.get("name") or "").strip(),
                label=(body.get("label") or "").strip(),
                icon=(body.get("icon") or "🎛️").strip(),
                actions=body.get("actions") or [],
            )
        return jsonify(r), (200 if r.get("ok") else 400)

    @app.route("/api/life/scenes/actions", methods=["POST"])
    @require_owner
    def api_scene_actions(claims):
        body = request.get_json(silent=True) or {}
        from app.features.scene_store import set_scene_actions

        with active_user_profile_context(_OWNER_PROFILE):
            r = set_scene_actions((body.get("name") or "").strip(), body.get("actions") or [])
        return jsonify(r), (200 if r.get("ok") else 400)

    @app.route("/api/life/scenes/apply", methods=["POST"])
    @require_owner
    def api_scene_apply(claims):
        body = request.get_json(silent=True) or {}
        from app.features.scene_store import apply_scene

        with active_user_profile_context(_OWNER_PROFILE):
            r = apply_scene((body.get("name") or "").strip())
        return jsonify(r), (200 if r.get("ok") else 404)

    @app.route("/api/life/scenes/delete", methods=["POST"])
    @require_owner
    def api_scene_delete(claims):
        body = request.get_json(silent=True) or {}
        from app.features.scene_store import delete_scene

        with active_user_profile_context(_OWNER_PROFILE):
            r = delete_scene((body.get("name") or "").strip())
        return jsonify(r), (200 if r.get("ok") else 404)

    @app.route("/api/life/focus", methods=["GET"])
    @require_auth
    def api_focus_status(claims):
        if not _owner(claims):
            return jsonify({"active": False, "demo": True}), 200
        from app.features.focus_store import focus_status

        with active_user_profile_context(_OWNER_PROFILE):
            return jsonify(focus_status()), 200

    @app.route("/api/life/focus/start", methods=["POST"])
    @require_owner
    def api_focus_start(claims):
        body = request.get_json(silent=True) or {}
        from app.features.focus_store import start_focus

        with active_user_profile_context(_OWNER_PROFILE):
            r = start_focus(
                focus_min=int(body.get("focus_min", 25) or 25),
                label=(body.get("label") or "").strip(),
                break_min=int(body.get("break_min", 0) or 0),
                cycles=int(body.get("cycles", 1) or 1),
                scene=(body.get("scene") or "").strip(),
                end_scene=(body.get("end_scene") or "").strip(),
            )
        return jsonify(r), (200 if r.get("ok") else 400)

    @app.route("/api/life/focus/stop", methods=["POST"])
    @require_owner
    def api_focus_stop(claims):
        body = request.get_json(silent=True) or {}
        from app.features.focus_store import stop_focus

        with active_user_profile_context(_OWNER_PROFILE):
            r = stop_focus(completed=not bool(body.get("cancel")))
        return jsonify(r), (200 if r.get("ok") else 404)

    @app.route("/api/life/focus/sounds", methods=["GET"])
    @require_owner
    def api_focus_sounds(claims):
        from app.features.focus_store import get_focus_sounds

        with active_user_profile_context(_OWNER_PROFILE):
            return jsonify(get_focus_sounds()), 200

    @app.route("/api/life/focus/sounds", methods=["POST"])
    @require_owner
    def api_focus_sound_set(claims):
        body = request.get_json(silent=True) or {}
        from app.features.focus_store import set_focus_sound

        with active_user_profile_context(_OWNER_PROFILE):
            r = set_focus_sound((body.get("event") or "").strip(), (body.get("melody") or "").strip())
        return jsonify(r), (200 if r.get("ok") else 400)
