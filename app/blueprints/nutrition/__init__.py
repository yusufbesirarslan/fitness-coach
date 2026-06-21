"""nutrition blueprint paketi — eski tek-dosya god-module'ün yerini alır.

`bp` burada tanımlanır; rota modülleri (plan/diary/meallog) bp'yi import edip
@bp.route ile kayıt olur. Alt modüller bp tanımlandıktan SONRA import edilir
(döngüsel import yok).
"""
from flask import Blueprint

bp = Blueprint("nutrition", __name__)

from app.blueprints.nutrition import diary, meallog, plan  # noqa: E402,F401
