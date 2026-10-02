"""nutrition blueprint paketi — eski tek-dosya god-module'ün yerini alır.

`bp` burada tanımlanır; rota modülleri (plan/diary/meallog) bp'yi import edip
@bp.route ile kayıt olur. Alt modüller bp tanımlandıktan SONRA import edilir
(döngüsel import yok).
"""
from flask import Blueprint, request

bp = Blueprint("nutrition", __name__)

# Per-user nutrition JSON reads: never stored by a shared cache or the bfcache
# (same policy as /nutrition-day-view, which sets it in its own view).
# /nutrition-plan/active is deliberately NOT here: under Playwright request
# interception a no-store answer never reports "finished", so the PR6 N7
# non-vacuity test (an injected duplicate plan read) would hang instead of
# failing. Over a real network the fetches resolve normally; revisit with that harness.
_PRIVATE_JSON_PATHS = frozenset({
    "/meal-log/today",
    "/api/diary/today",
})


@bp.after_request
def _private_no_store(response):
    if request.path in _PRIVATE_JSON_PATHS:
        response.headers["Cache-Control"] = "private, no-store"
    return response

from app.blueprints.nutrition import day_view, diary, meallog, plan  # noqa: E402,F401
