"""Markalı HTML e-posta şablonları — Email Sprint 3 (auth e-postaları).

Tek yeniden kullanılabilir kabuk (render_branded_email) AxisAI görünümünü taşır
— tek sütun responsive kart, resmi marka kilidi (holo "A" uygulama simgesi +
"AxisAI" adı), CTA butonu, footer'daki sosyal bağlantılar — ve e-posta-başına
fonksiyonlar içeriği doldurur. Tasarım otoritesi mobil uygulamanın Axis Signal
sistemidir (axisai-mobile docs/design/axis-signal/VISUAL_SYSTEM.md,
lib/app/theme/axisai_tokens.dart): renkler AxisAiPalette'in açık/koyu
token'larının birebir değerleridir. Eski Aurora Axis (mor/camgöbeği, "AXIS·AI")
paleti emekliye ayrıldı (BRAND-1). Açık tema satır-içi varsayılandır; koyu tema
`prefers-color-scheme` destekleyen istemcilerde (Apple Mail, iOS Mail) <style>
ile gelir, desteklemeyen istemci okunaklı açık temada kalır. Tüm temel stiller
e-posta istemcisi uyumluluğu için satır içidir. Her şablon (subject, html, text)
döndürür; düz-metin alternatifi HTML ile daima birlikte gider.

ÖNEMLİ — iki çalışma zamanı, tek kaynak: bu modül BİLEREK yalnızca stdlib
kullanır (os, html) ve app/* import ETMEZ, çünkü aynı dosya Cognito
CustomEmailSender Lambda'sında da çalışır (infra/cognito-email-sender/src/
altındaki kopya bayt-bayt aynı olmalı; tests/test_email_templates_sync.py
bunu zorlar). Değişiklikten sonra kopyayı güncelle:

    cp app/services/email_templates.py infra/cognito-email-sender/src/email_templates.py

Dil (LP-14): her şablon `language` alır ("tr" | "en"). Dili şablon SEÇMEZ —
çağıran, hesabın kanonik dilini (User.language) verir. Eksik/geçersiz/
desteklenmeyen dil DEFAULT_LANGUAGE'a (= app.i18n.DEFAULT_LOCALE, "tr") düşer;
app/* import edilemediği için değerler burada tekrarlanır ve
tests/test_auth_email_language.py eşitliklerini zorlar. Dil yalnızca sunumu
(konu, gövde, <html lang>) değiştirir; kod, bağlantı ve alıcı dilden bağımsızdır.

Güvenlik: doğrulama/sıfırlama kodları YALNIZCA gövdede yer alır, konu satırında
ASLA bulunmaz (konu satırları loglanır). Gizli önizleme metni (preheader) kodu
TAŞIMAZ ve HTML gelen-kutusu önizlemesini doldurur, böylece HTML'den önizleme
çıkaran istemcide kod önizleme satırına düşmez (düz-metin alternatifi bilerek
değişmedi; ondan önizleme çıkaran istemci kodu yine görebilir). Kullanıcıdan gelen `name` HTML-escape edilir (Cognito 'name'
attribute'u kullanıcı girdisidir). Tek uzak kaynak, landing'in zaten herkese
açık sunduğu statik uygulama simgesidir — alıcıya özel parametre taşımaz
(izleme pikseli DEĞİLDİR); görsel kapalı istemcide ad metin olarak kalır.
"""
import html as _html
import os

# Axis Signal token'ları (AxisAiPalette.light / .dark).
_CANVAS = "#F3F5FA"        # canvas
_SURFACE = "#FFFFFF"       # surface.1 — kart
_SURFACE_2 = "#EEF1F8"     # surface.2 — kod kutusu
_BORDER = "#DCE1EC"        # border.subtle
_TEXT = "#0A1020"          # text.primary
_MUTED = "#4A5470"         # text.secondary
_AXIS = "#2A62F5"          # brand.axis — TEK birincil dolgu (beyaz metin 5.04:1)
_AXIS_TEXT = "#2453D9"     # brand.axisText — bağlantılar
_ON_AXIS = "#FFFFFF"       # onAxis

_DARK_CANVAS = "#080D1A"
_DARK_SURFACE = "#0F1628"
_DARK_SURFACE_2 = "#151E33"
_DARK_BORDER = "#222D47"
_DARK_TEXT = "#F1F4FB"
_DARK_MUTED = "#A4AECA"
_DARK_AXIS_TEXT = "#84A9FF"

# Okuma yüzü platform fontudur (mobil: SF); uzak web fontu YOK (Resend uyarısı).
_FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,"
         "sans-serif")
_MONO = "'SF Mono',SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"

# Resmi işaret: App Store simgesi (#91) ile aynı holo "A" — landing'in favicon/
# og:image olarak sunduğu statik dosya. Yeniden renklendirilmez, kırpılmaz.
BRAND_MARK_URL = "https://www.axisaiapp.com/icon-holo.png"

# Yalnızca landing footer'ındaki gerçek profiller (LinkedIn orada hâlâ '#').
_SOCIAL_LINKS = (
    ("X", "https://x.com/axisaiapp"),
    ("Instagram", "https://www.instagram.com/axisai.app/"),
)

# CTA bağlantılarının tabanı. Flask tarafında .env'den, Lambda tarafında SAM
# parametresinden gelir; ikisinde de yoksa canlı site varsayılır.
APP_BASE_URL = os.environ.get("APP_BASE_URL", "https://www.axisaiapp.com").rstrip("/")

# app.i18n.AVAILABLE_LOCALES / DEFAULT_LOCALE ile aynı olmalı (test zorlar).
SUPPORTED_LANGUAGES = ("tr", "en")
DEFAULT_LANGUAGE = "tr"


def _lang(language):
    """Desteklenen dil aynen; eksik/geçersiz/desteklenmeyen dil → DEFAULT_LANGUAGE."""
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


# Koyu tema + dar ekran iyileştirmeleri. Ayrı <style> blokları: Gmail
# desteklemediği bir seçici gördüğünde bütün bloğu atabilir, öznitelik
# seçicileri (Apple veri dedektörleri) bu yüzden kendi bloğundadır.
_HEAD_STYLES = (
    '<style>'
    ':root{color-scheme:light dark;supported-color-schemes:light dark}'
    '@media (prefers-color-scheme:dark){'
    '.ax-canvas{background-color:%(d_canvas)s!important}'
    '.ax-card{background-color:%(d_surface)s!important;border-color:%(d_border)s!important}'
    '.ax-code{background-color:%(d_surface_2)s!important;border-color:%(d_border)s!important}'
    '.ax-rule{border-color:%(d_border)s!important}'
    '.ax-text{color:%(d_text)s!important}'
    '.ax-muted{color:%(d_muted)s!important}'
    '.ax-link{color:%(d_axis_text)s!important}'
    '}'
    '@media only screen and (max-width:600px){'
    '.ax-pad{padding-left:24px!important;padding-right:24px!important}'
    '.ax-h1{font-size:22px!important;line-height:28px!important}'
    '}'
    '</style>'
    '<style>'
    'a[x-apple-data-detectors]{color:inherit!important;text-decoration:none!important;'
    'font-size:inherit!important;font-family:inherit!important;'
    'font-weight:inherit!important;line-height:inherit!important}'
    '</style>'
    # Outlook (Word motoru) -apple-system'i tanımaz → Times'a düşmesin.
    '<!--[if mso]><style>body,table,td,div,p,h1,a{font-family:Arial,Helvetica,'
    'sans-serif!important}.ax-digits{font-family:Consolas,\'Courier New\','
    'monospace!important}</style><![endif]-->'
) % {
    "d_canvas": _DARK_CANVAS, "d_surface": _DARK_SURFACE,
    "d_surface_2": _DARK_SURFACE_2, "d_border": _DARK_BORDER,
    "d_text": _DARK_TEXT, "d_muted": _DARK_MUTED, "d_axis_text": _DARK_AXIS_TEXT,
}


def _preheader(text):
    """Gizli gelen-kutusu önizlemesi; dolgu, gövdenin (kodun) önizlemeye sızmasını önler."""
    if not text:
        return ""
    return (
        '<div style="display:none;max-height:0;max-width:0;overflow:hidden;'
        'mso-hide:all;opacity:0;font-size:1px;line-height:1px;color:%s">'
        '%s%s</div>' % (_CANVAS, text, "&#847;&zwnj;&nbsp;" * 90)
    )


def render_branded_email(body_html, cta_label, cta_url, footer_extra_html="",
                         language=DEFAULT_LANGUAGE, preheader=""):
    """İçeriği ortak AxisAI kabuğuna sar; tam HTML dokümanı döndürür."""
    social = " &nbsp;&middot;&nbsp; ".join(
        '<a class="ax-muted" href="%s" style="color:%s;text-decoration:underline">%s</a>'
        % (url, _MUTED, name)
        for name, url in _SOCIAL_LINKS
    )
    return (
        '<!DOCTYPE html>'
        '<html lang="%(lang)s"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="x-apple-disable-message-reformatting">'
        '<meta name="format-detection" content="telephone=no,date=no,address=no,email=no">'
        '<meta name="color-scheme" content="light dark">'
        '<meta name="supported-color-schemes" content="light dark">'
        '<title>AxisAI</title>'
        '%(head_styles)s'
        '</head>'
        '<body class="ax-canvas" style="margin:0;padding:0;background-color:%(canvas)s;'
        '-webkit-text-size-adjust:100%%">'
        '%(preheader)s'
        '<table role="presentation" class="ax-canvas" width="100%%" cellpadding="0"'
        ' cellspacing="0" border="0" bgcolor="%(canvas)s" style="background-color:%(canvas)s">'
        '<tr><td align="center" style="padding:32px 12px">'
        '<!--[if mso]><table role="presentation" width="560" cellpadding="0" cellspacing="0"'
        ' border="0"><tr><td><![endif]-->'
        '<table role="presentation" class="ax-card" width="100%%" cellpadding="0"'
        ' cellspacing="0" border="0" bgcolor="%(surface)s" style="max-width:560px;'
        'background-color:%(surface)s;border:1px solid %(border)s;border-radius:24px;'
        'font-family:%(font)s">'
        # Marka kilidi: işaret + ad, aradaki boşluk işaretin 0.2 katı (BrandMark).
        '<tr><td class="ax-pad" style="padding:32px 32px 8px">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        '<td style="vertical-align:middle;padding:0 8px 0 0">'
        '<img src="%(mark)s" width="40" height="40" alt=""'
        ' style="display:block;width:40px;height:40px;border:0;outline:none"></td>'
        '<td class="ax-text" style="vertical-align:middle;font-family:%(font)s;'
        'font-size:19px;line-height:24px;font-weight:700;letter-spacing:-0.2px;'
        'color:%(text)s">AxisAI</td>'
        '</tr></table>'
        '</td></tr>'
        '<tr><td class="ax-pad ax-text" style="padding:24px 32px 8px;color:%(text)s;'
        'font-family:%(font)s;font-size:16px;line-height:24px">'
        '%(body)s'
        '</td></tr>'
        '<tr><td class="ax-pad" style="padding:8px 32px 32px">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        '<td bgcolor="%(axis)s" style="border-radius:16px;background-color:%(axis)s;'
        'mso-padding-alt:15px 28px">'
        '<a href="%(cta_url)s" style="display:inline-block;padding:15px 28px;'
        'font-family:%(font)s;font-size:16px;line-height:22px;font-weight:600;'
        'color:%(on_axis)s;text-decoration:none;border-radius:16px">%(cta_label)s</a>'
        '</td></tr></table>'
        '</td></tr>'
        '<tr><td class="ax-pad ax-muted ax-rule" style="padding:24px 32px 32px;'
        'border-top:1px solid %(border)s;color:%(muted)s;font-family:%(font)s;'
        'font-size:13px;line-height:20px">'
        '%(footer_extra)s'
        '<div>%(social)s</div>'
        '<div style="padding-top:4px">&copy; 2026 AxisAI &middot; Find your axis.</div>'
        '</td></tr>'
        '</table>'
        '<!--[if mso]></td></tr></table><![endif]-->'
        '</td></tr></table></body></html>'
    ) % {
        "canvas": _CANVAS, "surface": _SURFACE, "border": _BORDER, "text": _TEXT,
        "muted": _MUTED, "axis": _AXIS, "on_axis": _ON_AXIS, "font": _FONT,
        "mark": BRAND_MARK_URL, "head_styles": _HEAD_STYLES,
        "preheader": _preheader(preheader), "body": body_html,
        "cta_label": cta_label, "cta_url": cta_url,
        "footer_extra": footer_extra_html, "social": social,
        "lang": _lang(language),
    }


def _code_block(code, label):
    """Tek kullanımlık kodu belirgin göster: büyük, aralıklı, eş-aralıklı rakamlar."""
    return (
        '<div class="ax-code" style="margin:0 0 20px;background-color:%(surface_2)s;'
        'border:1px solid %(border)s;border-radius:16px;padding:16px 20px;'
        'text-align:center">'
        '<div class="ax-muted" style="color:%(muted)s;font-size:13px;line-height:18px;'
        'font-weight:500">%(label)s</div>'
        # padding-left = letter-spacing: son rakamın ardındaki boşluğu dengeler.
        '<div class="ax-text ax-digits" style="padding:6px 0 0 8px;font-family:%(mono)s;font-size:32px;'
        'line-height:40px;letter-spacing:8px;font-weight:700;color:%(text)s">%(code)s</div>'
        '</div>'
    ) % {"surface_2": _SURFACE_2, "border": _BORDER, "muted": _MUTED, "text": _TEXT,
         "mono": _MONO, "label": label, "code": _html.escape(str(code))}


_H1 = ('<h1 class="ax-text ax-h1" style="margin:0 0 12px;font-family:%s;font-size:24px;'
       'line-height:30px;font-weight:700;letter-spacing:-0.2px;color:%s">%%s</h1>'
       % (_FONT, _TEXT))
_P = '<p style="margin:0 0 20px">%s</p>'
_NOTE = ('<p class="ax-muted" style="margin:0 0 8px;color:%s;font-size:14px;'
         'line-height:20px">%%s</p>' % _MUTED)


# Dil-başına metinler. "tr" değerleri LP-14 öncesi metinlerin birebir aynısıdır;
# "en" aynı olayı aynı güvenlik anlamıyla söyler. Buradaki metinler statiktir —
# kullanıcı girdisi yalnızca escape'lenmiş isim ve kod olarak girer.
_COPY = {
    "tr": {
        "greet_named": "Merhaba %s,",
        "greet": "Merhaba,",
        "verification": {
            "subject": "AxisAI — e-posta doğrulama kodun",
            "text": (
                "%(greet)s\n\n"
                "AxisAI hesabını doğrulamak için kodun: %(code)s\n\n"
                "Kodu %(base)s/verify sayfasına gir.\n\n"
                "Bu isteği sen yapmadıysan bu e-postayı yok sayabilirsin.\n"),
            "title": "E-postanı doğrula",
            "intro": "AxisAI hesabını doğrulamak için kodun aşağıda.",
            "note": "Bu isteği sen yapmadıysan bu e-postayı yok sayabilirsin.",
            "code_label": "Doğrulama kodu",
            "cta": "Hesabını doğrula",
        },
        "reset": {
            "subject": "AxisAI — şifre sıfırlama kodun",
            "text": (
                "%(greet)s\n\n"
                "Şifreni sıfırlamak için kodun: %(code)s\n\n"
                "Kodu %(base)s/reset-password sayfasına gir. Kod kısa süreliğine geçerlidir.\n\n"
                "Bu isteği sen yapmadıysan şifren değişmedi; bu e-postayı yok sayabilirsin.\n"),
            "title": "Şifreni sıfırla",
            "intro": "şifreni sıfırlamak için kodun aşağıda. Kod kısa süreliğine geçerlidir.",
            "note": "Bu isteği sen yapmadıysan şifren değişmedi; bu e-postayı yok sayabilirsin.",
            "code_label": "Sıfırlama kodu",
            "cta": "Şifreni sıfırla",
        },
        "welcome": {
            "subject_named": "AxisAI'ye hoş geldin, %s!",
            "subject": "AxisAI'ye hoş geldin!",
            "text": (
                "%(greet)s\n\n"
                "E-postan doğrulandı, hesabın hazır!\n\n"
                "AxisAI ile hedefine uygun beslenme ve antrenman planları oluşturabilir, "
                "ilerlemeni takip edebilir ve AI koçunla her an konuşabilirsin.\n\n"
                "Giriş yap: %(base)s/login\n"),
            "title": "Hoş geldin%(comma_name)s!",
            "intro": (
                "E-postan doğrulandı, hesabın hazır. "
                "AxisAI ile hedefine uygun beslenme ve antrenman planları oluşturabilir, "
                "ilerlemeni takip edebilir ve AI koçunla her an konuşabilirsin."),
            "cta": "Giriş yap",
        },
        "password_changed": {
            "subject": "AxisAI — şifren değiştirildi",
            "text": (
                "%(greet)s\n\n"
                "Hesabının şifresi az önce değiştirildi.\n\n"
                "Bu işlemi sen yaptıysan yapman gereken bir şey yok.\n"
                "Sen yapmadıysan hemen şifreni sıfırla (%(base)s/forgot-password) ve "
                "hello@axisaiapp.com adresinden bize ulaş.\n"),
            "title": "Şifren değiştirildi",
            "intro": "hesabının şifresi az önce değiştirildi.",
            "note": ("Bu işlemi sen yaptıysan yapman gereken bir şey yok. Sen yapmadıysan "
                     "hemen şifreni sıfırla ve bize ulaş."),
            "support": "Soruların için:",
            "cta": "Şifreni sıfırla",
        },
    },
    "en": {
        "greet_named": "Hi %s,",
        "greet": "Hi,",
        "verification": {
            "subject": "AxisAI — your email verification code",
            "text": (
                "%(greet)s\n\n"
                "Your code to verify your AxisAI account: %(code)s\n\n"
                "Enter the code at %(base)s/verify.\n\n"
                "If you didn't request this, you can ignore this email.\n"),
            "title": "Verify your email",
            "intro": "here is your code to verify your AxisAI account.",
            "note": "If you didn't request this, you can ignore this email.",
            "code_label": "Verification code",
            "cta": "Verify your account",
        },
        "reset": {
            "subject": "AxisAI — your password reset code",
            "text": (
                "%(greet)s\n\n"
                "Your code to reset your password: %(code)s\n\n"
                "Enter the code at %(base)s/reset-password. The code is valid for a short time.\n\n"
                "If you didn't request this, your password has not changed; "
                "you can ignore this email.\n"),
            "title": "Reset your password",
            "intro": "here is your code to reset your password. The code is valid for a short time.",
            "note": ("If you didn't request this, your password has not changed; "
                     "you can ignore this email."),
            "code_label": "Reset code",
            "cta": "Reset your password",
        },
        "welcome": {
            "subject_named": "Welcome to AxisAI, %s!",
            "subject": "Welcome to AxisAI!",
            "text": (
                "%(greet)s\n\n"
                "Your email is verified and your account is ready!\n\n"
                "With AxisAI you can create nutrition and training plans that fit your goal, "
                "track your progress and talk to your AI coach anytime.\n\n"
                "Sign in: %(base)s/login\n"),
            "title": "Welcome%(comma_name)s!",
            "intro": (
                "Your email is verified and your account is ready. "
                "With AxisAI you can create nutrition and training plans that fit your goal, "
                "track your progress and talk to your AI coach anytime."),
            "cta": "Sign in",
        },
        "password_changed": {
            "subject": "AxisAI — your password was changed",
            "text": (
                "%(greet)s\n\n"
                "Your account password was just changed.\n\n"
                "If you made this change, there's nothing you need to do.\n"
                "If you didn't, reset your password right away (%(base)s/forgot-password) and "
                "contact us at hello@axisaiapp.com.\n"),
            "title": "Your password was changed",
            "intro": "your account password was just changed.",
            "note": ("If you made this change, there's nothing you need to do. If you didn't, "
                     "reset your password right away and contact us."),
            "support": "Questions?",
            "cta": "Reset your password",
        },
    },
}


def _greeting(name, language=None):
    """Escape'lenmiş kişisel selamlama; isim yoksa nötr selamlama."""
    copy = _COPY[_lang(language)]
    name = (name or "").strip()
    return copy["greet_named"] % _html.escape(name) if name else copy["greet"]


def _code_email(kind, name, code, language, path):
    """Kod taşıyan iki e-postanın (doğrulama, sıfırlama) ortak gövdesi."""
    lang = _lang(language)
    copy = _COPY[lang][kind]
    greet = _greeting(name, lang)
    text = copy["text"] % {"greet": greet, "code": code, "base": APP_BASE_URL}
    lead = "%s %s" % (greet, copy["intro"])
    body_html = (_H1 % copy["title"] + _P % lead
                 + _code_block(code, copy["code_label"]) + _NOTE % copy["note"])
    html = render_branded_email(body_html, copy["cta"], APP_BASE_URL + path,
                                language=lang, preheader=lead)
    return copy["subject"], html, text


def verification_code_email(name, code, language=None):
    """Kayıt/yeniden-gönderim doğrulama kodu e-postası. (subject, html, text) döndürür."""
    return _code_email("verification", name, code, language, "/verify")


def reset_code_email(name, code, language=None):
    """Şifre sıfırlama kodu e-postası. (subject, html, text) döndürür."""
    return _code_email("reset", name, code, language, "/reset-password")


def welcome_email(name, language=None):
    """Hesap doğrulaması sonrası hoş geldin e-postası. (subject, html, text) döndürür."""
    lang = _lang(language)
    copy = _COPY[lang]["welcome"]
    raw = (name or "").strip()
    safe = _html.escape(raw)
    subject = copy["subject_named"] % raw if raw else copy["subject"]
    text = copy["text"] % {"greet": _greeting(name, lang), "base": APP_BASE_URL}
    title = copy["title"] % {"comma_name": (", " + safe) if safe else ""}
    body_html = _H1 % title + _P % copy["intro"]
    html = render_branded_email(body_html, copy["cta"], APP_BASE_URL + "/login",
                                language=lang, preheader=copy["intro"])
    return subject, html, text


def password_changed_email(name, language=None):
    """Şifre değişikliği sonrası güvenlik bildirimi. (subject, html, text) döndürür."""
    lang = _lang(language)
    copy = _COPY[lang]["password_changed"]
    greet = _greeting(name, lang)
    text = copy["text"] % {"greet": greet, "base": APP_BASE_URL}
    lead = "%s %s" % (greet, copy["intro"])
    body_html = _H1 % copy["title"] + _P % lead + _NOTE % copy["note"]
    footer_extra = (
        '<div style="padding-bottom:4px">%s <a class="ax-link" '
        'href="mailto:hello@axisaiapp.com" style="color:%s;text-decoration:underline">'
        'hello@axisaiapp.com</a></div>'
        % (copy["support"], _AXIS_TEXT)
    )
    html = render_branded_email(body_html, copy["cta"],
                                APP_BASE_URL + "/forgot-password", footer_extra,
                                language=lang, preheader=lead)
    return copy["subject"], html, text
