"""Markalı HTML e-posta şablonları — Email Sprint 3 (auth e-postaları).

Tek yeniden kullanılabilir kabuk (render_branded_email) AxisAI görünümünü taşır
— koyu tema, tek sütun responsive, CTA butonu, landing footer'ındaki sosyal
bağlantılar — ve e-posta-başına fonksiyonlar içeriği doldurur. Palet, landing
sitesinin Aurora Axis token'larını (styles.css :root) aynalar; landing repo'daki
backend/src/email_templates.py kabuğunun birebir taşınmış halidir. Tüm stiller
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
ASLA bulunmaz (konu satırları loglanır). Kullanıcıdan gelen `name` HTML-escape
edilir (Cognito 'name' attribute'u kullanıcı girdisidir).
"""
import html as _html
import os

_BG = "#07070D"        # --ax-bg
_SURFACE = "#0F1020"   # --ax-surface
_BORDER = "#23264a"
_TEXT = "#ECEDF6"      # --ax-text
_MUTED = "#9AA0C0"     # --ax-text-2
_VIOLET = "#7C5CFF"    # --ax-violet

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


def render_branded_email(body_html, cta_label, cta_url, footer_extra_html="",
                         language=DEFAULT_LANGUAGE):
    """İçeriği ortak AxisAI kabuğuna sar; tam HTML dokümanı döndürür."""
    social = " &nbsp;&middot;&nbsp; ".join(
        '<a href="%s" style="color:%s;text-decoration:underline">%s</a>' % (url, _MUTED, name)
        for name, url in _SOCIAL_LINKS
    )
    return (
        '<!DOCTYPE html>'
        '<html lang="%(lang)s"><body style="margin:0;padding:0;background:%(bg)s">'
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0"'
        ' style="background:%(bg)s;padding:32px 16px"><tr><td align="center">'
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0"'
        ' style="max-width:560px;background:%(surface)s;border:1px solid %(border)s;'
        'border-radius:16px;font-family:Arial,Helvetica,sans-serif">'
        '<tr><td style="padding:32px 32px 8px">'
        '<div style="font-size:18px;font-weight:bold;letter-spacing:2px;color:%(text)s">'
        'AXIS<span style="color:%(violet)s">AI</span></div>'
        '</td></tr>'
        '<tr><td style="padding:16px 32px 8px;color:%(text)s;font-size:15px;line-height:1.6">'
        '%(body)s'
        '</td></tr>'
        '<tr><td style="padding:8px 32px 32px">'
        '<a href="%(cta_url)s" style="display:inline-block;background:%(violet)s;'
        'color:#ffffff;text-decoration:none;font-weight:bold;font-size:14px;'
        'padding:12px 24px;border-radius:10px">%(cta_label)s</a>'
        '</td></tr>'
        '<tr><td style="padding:20px 32px 28px;border-top:1px solid %(border)s;'
        'color:%(muted)s;font-size:12px;line-height:1.8">'
        '%(footer_extra)s'
        '<div>%(social)s</div>'
        '<div>&copy; 2026 AxisAI &middot; Find your axis.</div>'
        '</td></tr>'
        '</table></td></tr></table></body></html>'
    ) % {
        "bg": _BG, "surface": _SURFACE, "border": _BORDER, "text": _TEXT,
        "muted": _MUTED, "violet": _VIOLET, "body": body_html,
        "cta_label": cta_label, "cta_url": cta_url,
        "footer_extra": footer_extra_html, "social": social,
        "lang": _lang(language),
    }


def _code_block(code, label):
    """Tek kullanımlık kodu belirgin göster (waitlist member-code kutusu deseni)."""
    return (
        '<div style="margin:0 0 16px;background:%(bg)s;border:1px solid %(border)s;'
        'border-radius:10px;padding:14px 18px">'
        '<div style="color:%(muted)s;font-size:11px;letter-spacing:1px;'
        'text-transform:uppercase">%(label)s</div>'
        '<div style="font-family:Consolas,Menlo,monospace;font-size:28px;'
        'letter-spacing:6px;font-weight:bold;color:%(violet)s">%(code)s</div>'
        '</div>'
    ) % {"bg": _BG, "border": _BORDER, "muted": _MUTED, "violet": _VIOLET,
         "label": label, "code": _html.escape(str(code))}


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
    body_html = (
        '<h1 style="margin:0 0 12px;font-size:22px;color:%(text)s">%(title)s</h1>'
        '<p style="margin:0 0 16px">%(greet)s %(intro)s</p>'
        '%(code_block)s'
        '<p style="margin:0 0 8px;color:%(muted)s;font-size:13px">%(note)s</p>'
    ) % {"text": _TEXT, "muted": _MUTED, "greet": greet, "title": copy["title"],
         "intro": copy["intro"], "note": copy["note"],
         "code_block": _code_block(code, copy["code_label"])}
    html = render_branded_email(body_html, copy["cta"], APP_BASE_URL + path,
                                language=lang)
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
    body_html = (
        '<h1 style="margin:0 0 12px;font-size:22px;color:%(text)s">%(title)s</h1>'
        '<p style="margin:0 0 16px">%(intro)s</p>'
    ) % {"text": _TEXT, "intro": copy["intro"],
         "title": copy["title"] % {"comma_name": (", " + safe) if safe else ""}}
    html = render_branded_email(body_html, copy["cta"], APP_BASE_URL + "/login",
                                language=lang)
    return subject, html, text


def password_changed_email(name, language=None):
    """Şifre değişikliği sonrası güvenlik bildirimi. (subject, html, text) döndürür."""
    lang = _lang(language)
    copy = _COPY[lang]["password_changed"]
    greet = _greeting(name, lang)
    text = copy["text"] % {"greet": greet, "base": APP_BASE_URL}
    body_html = (
        '<h1 style="margin:0 0 12px;font-size:22px;color:%(text)s">%(title)s</h1>'
        '<p style="margin:0 0 16px">%(greet)s %(intro)s</p>'
        '<p style="margin:0 0 8px;color:%(muted)s;font-size:13px">%(note)s</p>'
    ) % {"text": _TEXT, "muted": _MUTED, "greet": greet, "title": copy["title"],
         "intro": copy["intro"], "note": copy["note"]}
    footer_extra = (
        '<div>%s <a href="mailto:hello@axisaiapp.com" '
        'style="color:%s;text-decoration:underline">hello@axisaiapp.com</a></div>'
        % (copy["support"], _MUTED)
    )
    html = render_branded_email(body_html, copy["cta"],
                                APP_BASE_URL + "/forgot-password", footer_extra,
                                language=lang)
    return copy["subject"], html, text
