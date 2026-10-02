"""nutrition blueprint paketi — eski tek-dosya god-module'ün yerini alır.

`bp` burada tanımlanır; rota modülleri (plan/diary/meallog) bp'yi import edip
@bp.route ile kayıt olur. Alt modüller bp tanımlandıktan SONRA import edilir
(döngüsel import yok).
"""
from flask import Blueprint, request

bp = Blueprint("nutrition", __name__)

# Per-user nutrition JSON reads: never stored by a shared cache or the bfcache
# (same policy as /nutrition-day-view, which sets it in its own view).
_PRIVATE_JSON_PATHS = frozenset({
    "/meal-log/today",
    "/nutrition-plan/active",
    "/api/diary/today",
})


@bp.after_request
def _private_no_store(response):
    if request.path in _PRIVATE_JSON_PATHS:
        response.headers["Cache-Control"] = "private, no-store"
    return response

from app.blueprints.nutrition import day_view, diary, meallog, plan  # noqa: E402,F401
