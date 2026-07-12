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


def render_branded_email(body_html, cta_label, cta_url, footer_extra_html=""):
    """İçeriği ortak AxisAI kabuğuna sar; tam HTML dokümanı döndürür."""
    social = " &nbsp;&middot;&nbsp; ".join(
        '<a href="%s" style="color:%s;text-decoration:underline">%s</a>' % (url, _MUTED, name)
        for name, url in _SOCIAL_LINKS
    )
    return (
        '<!DOCTYPE html>'
        '<html lang="tr"><body style="margin:0;padding:0;background:%(bg)s">'
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


def _greeting(name):
    """Escape'lenmiş kişisel selamlama; isim yoksa nötr selamlama."""
    name = (name or "").strip()
    return "Merhaba %s," % _html.escape(name) if name else "Merhaba,"


def verification_code_email(name, code):
    """Kayıt/yeniden-gönderim doğrulama kodu e-postası. (subject, html, text) döndürür."""
    subject = "AxisAI — e-posta doğrulama kodun"
    text = (
        "%s\n\n"
        "AxisAI hesabını doğrulamak için kodun: %s\n\n"
        "Kodu %s/verify sayfasına gir.\n\n"
        "Bu isteği sen yapmadıysan bu e-postayı yok sayabilirsin.\n"
    ) % (_greeting(name), code, APP_BASE_URL)
    body_html = (
        '<h1 style="margin:0 0 12px;font-size:22px;color:%(text)s">E-postanı doğrula</h1>'
        '<p style="margin:0 0 16px">%(greet)s AxisAI hesabını doğrulamak için kodun aşağıda.</p>'
        '%(code_block)s'
        '<p style="margin:0 0 8px;color:%(muted)s;font-size:13px">'
        'Bu isteği sen yapmadıysan bu e-postayı yok sayabilirsin.</p>'
    ) % {"text": _TEXT, "muted": _MUTED, "greet": _greeting(name),
         "code_block": _code_block(code, "Doğrulama kodu")}
    html = render_branded_email(body_html, "Hesabını doğrula", APP_BASE_URL + "/verify")
    return subject, html, text


def reset_code_email(name, code):
    """Şifre sıfırlama kodu e-postası. (subject, html, text) döndürür."""
    subject = "AxisAI — şifre sıfırlama kodun"
    text = (
        "%s\n\n"
        "Şifreni sıfırlamak için kodun: %s\n\n"
        "Kodu %s/reset-password sayfasına gir. Kod kısa süreliğine geçerlidir.\n\n"
        "Bu isteği sen yapmadıysan şifren değişmedi; bu e-postayı yok sayabilirsin.\n"
    ) % (_greeting(name), code, APP_BASE_URL)
    body_html = (
        '<h1 style="margin:0 0 12px;font-size:22px;color:%(text)s">Şifreni sıfırla</h1>'
        '<p style="margin:0 0 16px">%(greet)s şifreni sıfırlamak için kodun aşağıda. '
        'Kod kısa süreliğine geçerlidir.</p>'
        '%(code_block)s'
        '<p style="margin:0 0 8px;color:%(muted)s;font-size:13px">'
        'Bu isteği sen yapmadıysan şifren değişmedi; bu e-postayı yok sayabilirsin.</p>'
    ) % {"text": _TEXT, "muted": _MUTED, "greet": _greeting(name),
         "code_block": _code_block(code, "Sıfırlama kodu")}
    html = render_branded_email(body_html, "Şifreni sıfırla", APP_BASE_URL + "/reset-password")
    return subject, html, text


def welcome_email(name):
    """Hesap doğrulaması sonrası hoş geldin e-postası. (subject, html, text) döndürür."""
    safe = _html.escape((name or "").strip())
    subject = ("AxisAI'ye hoş geldin, %s!" % (name or "").strip()) if (name or "").strip() \
        else "AxisAI'ye hoş geldin!"
    text = (
        "%s\n\n"
        "E-postan doğrulandı, hesabın hazır!\n\n"
        "AxisAI ile hedefine uygun beslenme ve antrenman planları oluşturabilir, "
        "ilerlemeni takip edebilir ve AI koçunla her an konuşabilirsin.\n\n"
        "Giriş yap: %s/login\n"
    ) % (_greeting(name), APP_BASE_URL)
    body_html = (
        '<h1 style="margin:0 0 12px;font-size:22px;color:%(text)s">Hoş geldin%(comma_name)s!</h1>'
        '<p style="margin:0 0 16px">E-postan doğrulandı, hesabın hazır. '
        'AxisAI ile hedefine uygun beslenme ve antrenman planları oluşturabilir, '
        'ilerlemeni takip edebilir ve AI koçunla her an konuşabilirsin.</p>'
    ) % {"text": _TEXT, "comma_name": (", " + safe) if safe else ""}
    html = render_branded_email(body_html, "Giriş yap", APP_BASE_URL + "/login")
    return subject, html, text


def password_changed_email(name):
    """Şifre değişikliği sonrası güvenlik bildirimi. (subject, html, text) döndürür."""
    subject = "AxisAI — şifren değiştirildi"
    text = (
        "%s\n\n"
        "Hesabının şifresi az önce değiştirildi.\n\n"
        "Bu işlemi sen yaptıysan yapman gereken bir şey yok.\n"
        "Sen yapmadıysan hemen şifreni sıfırla (%s/forgot-password) ve "
        "hello@axisaiapp.com adresinden bize ulaş.\n"
    ) % (_greeting(name), APP_BASE_URL)
    body_html = (
        '<h1 style="margin:0 0 12px;font-size:22px;color:%(text)s">Şifren değiştirildi</h1>'
        '<p style="margin:0 0 16px">%(greet)s hesabının şifresi az önce değiştirildi.</p>'
        '<p style="margin:0 0 8px;color:%(muted)s;font-size:13px">'
        'Bu işlemi sen yaptıysan yapman gereken bir şey yok. Sen yapmadıysan '
        'hemen şifreni sıfırla ve bize ulaş.</p>'
    ) % {"text": _TEXT, "muted": _MUTED, "greet": _greeting(name)}
    footer_extra = (
        '<div>Soruların için: <a href="mailto:hello@axisaiapp.com" '
        'style="color:%s;text-decoration:underline">hello@axisaiapp.com</a></div>' % _MUTED
    )
    html = render_branded_email(body_html, "Şifreni sıfırla",
                                APP_BASE_URL + "/forgot-password", footer_extra)
    return subject, html, text
