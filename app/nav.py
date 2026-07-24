"""Kanonik gezinme sözleşmesi — AxisAI UIUX Sprint 1 PR1.

TEK doğruluk kaynağı: masaüstü başlık sekmeleri, mobil alt çubuk ve ikincil
çekmece aynı bu listeden beslenir (elle bakımlı ayrı listeler YOK). Modül
YALNIZCA sunumsal meta veri + kanonik rota referansları içerir — hiçbir iş
kuralı, yetki, abonelik, onboarding ya da özellik-hak mantığı YOK. Backend'in
sahip olduğu kuralları istemci YENİDEN kurmaz; aktiflik sunucu-tarafı `nav_active`
kimliğinden türetilir (çevrilmiş etiketten, ikon adından ya da DOM metninden
DEĞİL). Rotalar depodaki mevcut kanonik uçlardan alınır; yeni rota UYDURULMAZ.

Aktiflik eşlemesi çocuk-rotaları açıkça kapsar: `active_when` bir hedefi
etkinleştiren tüm `nav_active` kimliklerini listeler (ör. Plan hem 'plan' hem
'training' işaretinde aktiftir), böylece string-önek/DOM tabanlı kırılgan
tespitten kaçınılır.
"""
from __future__ import annotations

from types import MappingProxyType


def _freeze(rows):
    """Sözlükleri salt-okunur görünümlere çevir — sözleşme çalışma zamanında
    kazara değiştirilemesin (tek doğruluk kaynağı bütünlüğü)."""
    return tuple(MappingProxyType(dict(r)) for r in rows)


# ── Birincil (beta-kritik) hedefler ──
# SIRA sözleşmedir: Today · Plan · Coach · Progress. Bu dört hedef beta birincil
# yolculuğudur; başka hiçbir hedef bu tier'de yer almaz.
PRIMARY = _freeze([
    {"id": "today",    "label_key": "nav.today",    "path": "/",              "icon": "today",    "tier": "primary", "active_when": ("today", "home")},
    {"id": "plan",     "label_key": "nav.plan",     "path": "/training",      "icon": "plan",     "tier": "primary", "active_when": ("plan", "training")},
    {"id": "coach",    "label_key": "nav.coach",    "path": "/coach",         "icon": "coach",    "tier": "primary", "active_when": ("coach",)},
    {"id": "progress", "label_key": "nav.progress", "path": "/progress-page", "icon": "progress", "tier": "primary", "active_when": ("progress",)},
])

# ── İkincil tier ──
# Nutrition birincilden indirildi (product). Community (feed/friends/leaderboard/
# quests/challenges/gallery) beta-kritik yolculukta DEĞİL → burada, birincille
# yarışmaz. Utility (notifications/profile/logout) ürün yolculuğuna karışmaz.
# Hiçbir rota SİLİNMEZ; deep-link'ler korunur — yalnızca nerede yüzeye çıktığı
# değişir. Sıra çekmecedeki görünüm sırasıdır.
SECONDARY = _freeze([
    {"id": "nutrition",    "label_key": "nav.nutrition",             "path": "/nutrition",          "icon": "nutrition",   "tier": "product",   "active_when": ("nutrition",)},
    {"id": "notifications","label_key": "nav.notifications",         "path": "/notifications",      "icon": "bell",        "tier": "utility",   "active_when": ()},
    {"id": "friends",      "label_key": "nav.friends",               "path": "/friends",            "icon": "friends",     "tier": "community", "active_when": ()},
    {"id": "feed",         "label_key": "nav.feed",                  "path": "/feed",               "icon": "feed",        "tier": "community", "active_when": ()},
    {"id": "leaderboard",  "label_key": "nav.club",                  "path": "/leaderboard",        "icon": "club",        "tier": "community", "active_when": ()},
    {"id": "quests",       "label_key": "nav.quests",                "path": "/quests",             "icon": "quests",      "tier": "community", "active_when": ()},
    {"id": "challenges",   "label_key": "nav.challenges",            "path": "/challenges",         "icon": "challenges",  "tier": "community", "active_when": ()},
    {"id": "gallery",      "label_key": "gallery.profile_link_title","path": "/pump-check-gallery", "icon": "gallery",     "tier": "community", "active_when": ()},
    {"id": "supplements",  "label_key": "nav.supplements",           "path": "/supplements",        "icon": "supplements", "tier": "product",   "active_when": ()},
    {"id": "premium",      "label_key": "nav.premium",               "path": "/premium",            "icon": "premium",     "tier": "product",   "active_when": ()},
    {"id": "profile",      "label_key": "nav.profile",               "path": "/edit-profile",       "icon": "profile",     "tier": "utility",   "active_when": ("profile",)},
    {"id": "logout",       "label_key": "nav.logout",                "path": "/logout",             "icon": "logout",      "tier": "utility",   "active_when": ()},
])

# nav_active kimliği → birincil hedef kimliği. YALNIZCA birincil hedefler indekse
# girer; ikincil/utility sayfalar hiçbir birincil sekmeyi etkinleştirmez.
_PRIMARY_ACTIVE_INDEX = MappingProxyType(
    {aw: d["id"] for d in PRIMARY for aw in d["active_when"]}
)


def primary_destinations():
    """Birincil tier — sabit sırayla (Today, Plan, Coach, Progress)."""
    return PRIMARY


def secondary_destinations():
    """İkincil tier — çekmece/hesap menüsü görünüm sırasıyla."""
    return SECONDARY


def resolve_active(nav_active):
    """Sayfanın `nav_active` işaretini bir BİRİNCİL hedef kimliğine eşle.

    Eşleşme yoksa None döner → Community/utility/Nutrition sayfaları hiçbir
    birincil sekmeyi yanlışlıkla aktif göstermez. Girdi None/bilinmeyen ise de
    güvenle None döner (bayrak/işaret eksikse sessizce sapmaz)."""
    if not nav_active:
        return None
    return _PRIMARY_ACTIVE_INDEX.get(nav_active)
