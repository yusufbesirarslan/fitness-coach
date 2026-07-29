"""Hafif i18n: tek JSON sözlük (locales/{tr,en}.json) + t() yardımcısı + locale
çözücü. Flask-Babel YOK, derleme adımı YOK; aynı sözlük Python, Jinja ({{ t(...) }})
ve JS (window.I18N) tarafından paylaşılır.

Anahtarlar nokta-ad-uzaylıdır (ör. "auth.login", "nav.quests"). Çevrilmemiş bir
anahtar sırayla `seçili dil → tr → anahtarın kendisi` ile çözülür; böylece henüz
göç etmemiş sayfalar Türkçe kalır ve eksik çeviri kırılma değil görünür hâle gelir.
"""
import json
import os

from flask import g, request, session

DEFAULT_LOCALE = "tr"
AVAILABLE_LOCALES = ("tr", "en")

_LOCALES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "locales")

_CATALOG = {}


def _load():
    """locales/*.json dosyalarını belleğe yükle (import'ta bir kez)."""
    cat = {}
    for loc in AVAILABLE_LOCALES:
        path = os.path.join(_LOCALES_DIR, "%s.json" % loc)
        try:
            with open(path, encoding="utf-8") as f:
                cat[loc] = json.load(f)
        except (OSError, ValueError):
            cat[loc] = {}
    return cat


_CATALOG = _load()


def reload_catalog():
    """Sözlüğü diskten yeniden oku (testlerde / sıcak geliştirmede kullanışlı)."""
    global _CATALOG
    _CATALOG = _load()
    return _CATALOG


def _norm(locale):
    return locale if locale in AVAILABLE_LOCALES else None


def current_locale():
    """Bu istekteki etkin dil (g.locale); yoksa varsayılan. App/istek bağlamı
    dışında çağrılırsa (ör. validators birim testleri) g erişimi RuntimeError
    atar — bu durumda sessizce varsayılana düşer."""
    try:
        loc = getattr(g, "locale", None)
    except RuntimeError:
        loc = None
    return loc if loc in AVAILABLE_LOCALES else DEFAULT_LOCALE


def t(key, locale=None, **kwargs):
    """key → çevrilmiş metin. locale verilmezse g.locale kullanılır. Değer
    str.format(**kwargs) ile interpolasyon destekler (ör. t('hi', name=x))."""
    loc = _norm(locale) or current_locale()
    val = _CATALOG.get(loc, {}).get(key)
    if val is None and loc != DEFAULT_LOCALE:
        val = _CATALOG.get(DEFAULT_LOCALE, {}).get(key)
    if val is None:
        val = key  # son çare: anahtarı göster → eksik çeviri görünür olur
    if kwargs:
        try:
            return val.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return val
    return val


def t_or(key, fallback, locale=None, **kwargs):
    """t() gibi ama anahtar HİÇBİR sözlükte yoksa `fallback` döner (anahtarın
    kendisi değil). Kanonik DB değerleri (ör. yeni bir quest_type'ın seed başlığı)
    katalogda karşılığı olmadığında ham anahtar yerine DB metnini göstermek için."""
    val = t(key, locale=locale, **kwargs)
    return fallback if val == key else val


def catalog(locale=None, *, exclude_prefixes=()):
    """Bir dilin TAM sözlüğü (tr tabanı üzerine seçili dil) — window.I18N'e
    enjekte etmek için. Böylece JS'te de eksik anahtar Türkçeye düşer."""
    loc = _norm(locale) or current_locale()
    merged = dict(_CATALOG.get(DEFAULT_LOCALE, {}))
    if loc != DEFAULT_LOCALE:
        merged.update(_CATALOG.get(loc, {}))
    prefixes = tuple(exclude_prefixes)
    if prefixes:
        merged = {
            key: value for key, value in merged.items()
            if not key.startswith(prefixes)
        }
    return merged


def resolve_locale():
    """before_request: g.locale'i çöz. Öncelik:
    1) Girişli kullanıcı → current_user.language
    2) Anonim → session['lang'] (kayıt-öncesi TR/EN seçimi burada saklanır)
    3) Varsayılan → 'tr'
    current_user importu döngü yaratmasın diye fonksiyon içinde yapılır."""
    if request.blueprint == "mobile_api":
        g.locale = DEFAULT_LOCALE
        return
    loc = None
    try:
        from flask_login import current_user
        if current_user.is_authenticated:
            loc = _norm(getattr(current_user, "language", None))
    except Exception:
        loc = None
    if loc is None:
        loc = _norm(session.get("lang"))
    g.locale = loc or DEFAULT_LOCALE


def set_locale(locale):
    """Anonim/oturum dilini session'a yaz; geçerliyse True döner."""
    loc = _norm(locale)
    if loc is None:
        return False
    session["lang"] = loc
    g.locale = loc
    return True
