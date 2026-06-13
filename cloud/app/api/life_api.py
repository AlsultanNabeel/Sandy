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

    # ── القراءة ─────────────────────────────────────────────────────────
    @app.route("/api/life/books", methods=["GET"])
    @require_auth
    def api_books(claims):
        if not _owner(claims):
            return jsonify({"items": _DEMO["books"], "demo": True, "stats": {"sessions": 4, "pages": 96, "minutes": 210}}), 200
        from app.features.reading_store import list_books, reading_stats

        with active_user_profile_context(_OWNER_PROFILE):
            items = list_books()
            stats = reading_stats(days=30)
        return jsonify({"items": items, "stats": stats, "demo": False}), 200

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
