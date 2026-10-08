"""Canonical restaurant-menu acquisition and analysis (LP15-C).

Transport-neutral home of the two authorities the web menu routes used to
carry inline, so the browser routes (`app/blueprints/menu.py`) and the native
route (`app/blueprints/mobile_menu.py`) run ONE path:

* `acquire_menu(url)` — URL admission, Google Drive dispatch, the scan cache,
  the bounded main-page + sub-page + WordPress acquisition and section
  extraction. Every network byte goes through the LP15-B1 boundary
  (`menu_fetch` → `menu_remote`): credential-free worker, no env/proxy/netrc,
  DNS + connected-peer admission, manual redirect re-admission, HTTPS
  downgrade rejection, one shared deadline/byte/request budget per call
  (`menu_operation`), bounded parsers, remote PDF/images refused.
* `analyze_menu_text(user_id, ...)` — categorized extraction (+ extraction
  cache), macro resolution (cache → FatSecret → LLM weights/macros → stated-gram
  re-estimate), plausibility/band checks and the deterministic fit score
  against the user's canonical remaining budget. Read-only: it reads
  `UserSession`/`MealLog` and writes nothing but the existing macro/extraction
  caches.

Failures are typed exceptions carrying a closed reason; each transport renders
them in its own contract (web: the historical status/strings, pinned by
`tests/test_menu_web_characterization.py`; native: the mobile envelope).

Remote menu response content must never be interpolated into server logs.
Use counts, fixed reason codes, exception types and redacted request URLs only.
"""
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests as http_req
from flask import current_app

from app.extensions import redis_client
from app.i18n import t
from app.models import MealLog, UserSession
from app.services import ai_spend_guard, nutrition_pipeline
from app.services.ai_nutrition import (
    MAX_MENU_ITEMS, _cap_items_round_robin, _estimate_macros_llm,
    _estimate_serving_weights_llm, _extract_categorized_items, _primary_dish_type,
)
from app.services.fatsecret import _get_fatsecret_token, _lookup_macros_fatsecret
from app.services.foodcache import _cache_macros, _get_cached_macros
from app.services.menu_extract import (
    _content_has_food_items, _discover_menu_links, _extract_framework_state,
    _extract_page_sections, _menu_score, _try_wordpress_api,
)
from app.services.menu_fetch import (
    _fetch_page, _is_google_drive_url, _process_google_drive_url, _validate_menu_url,
    loggable_url,
)
from app.services.menu_parse import bounded_soup, bounded_text, bound_sections
from app.services.menu_remote import menu_operation
from app.services.nutrition_targets import (
    derive_daily_macro_targets,
    remaining_macro_budget,
)
from app.timeutil import day_key


# Menü URL tarama önbelleği: aynı menü tekrar taranınca siteyi yeniden fetch
# etmeden döndürülür. Menü içeriği kullanıcıdan bağımsız → global (kullanıcıya
# özgü makro hesabı ayrı /api/menu/analyze adımında). redis_client None ise no-op.
MENU_SCAN_CACHE_PREFIX = "menu:scan:v2:"
# Native HTTPS-only acquisitions never follow an HTTP sub-page, so a web scan of
# the same HTTPS URL (which may have) is not admissible for them: own namespace.
MENU_SCAN_HTTPS_ONLY_CACHE_PREFIX = "menu:scan:v2:https-only:"
MENU_SCAN_CACHE_TTL = 6 * 3600  # 6 saat — web menüleri gün içinde nadiren değişir


# Çıkarım (kategorize yemek listesi) önbelleği: /api/menu/analyze'ın en pahalı
# adımı olan LLM çıkarımı kullanıcıdan BAĞIMSIZDIR (aynı metin + temperature=0 →
# aynı liste). Tarama önbelleği body_text'i 6 saat sabitlediğinden, aynı menüyü
# analiz eden sonraki istekler (aynı ya da farklı kullanıcı) çıkarımı Redis'ten
# alır; kullanıcıya özgü hedef/kalan/skor hesabı her istekte yeniden yapılır.
MENU_EXTRACT_CACHE_PREFIX = "menu:extract:v1:"
MENU_EXTRACT_CACHE_TTL = MENU_SCAN_CACHE_TTL  # tarama önbelleğiyle hizalı

# At most six subpages share the same fetch/deadline/aggregate budget.
MAX_SUB_PAGES = 6
_BODY_TEXT_MAX = 40000


# Makro kaynağı → güven skoru (0-1). Yanıt alanı EKLEMELİDİR — mevcut istemciler
# bilmediği alanları yok sayar. Skorlar kaynağın güvenilirlik sırasını yansıtır:
# doğrulanmış DB porsiyonu > önbellek > DB yoğunluğu × LLM ağırlığı ≈ beyan-gramajlı
# LLM > saf LLM > tür-varsayılanı ağırlıkla ölçekleme > veri yok.
_MACRO_CONFIDENCE = {
    "fatsecret_serving": 0.9,
    "cache": 0.8,
    "fatsecret_scaled": 0.7,
    "llm_stated_grams": 0.7,
    "llm": 0.6,
    "fatsecret_scaled_fallback": 0.45,
    "none": 0.0,
}


class MenuAcquisitionError(Exception):
    """Acquisition stopped. `reason` is a closed vocabulary:

    URL_INVALID (detail = canonical admission code), DRIVE (detail = the
    historical Drive failure string/JSON), FETCH_REJECTED (detail = the
    `MENU_*` boundary code), TIMEOUT, CONNECTION (detail = exception type name,
    upstream_status = HTTP status when the upstream answered), UNSUPPORTED_TYPE,
    PARSE_LIMIT (detail = `MENU_*` parser code), UNREADABLE.
    """

    def __init__(self, reason, detail=None, upstream_status=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.upstream_status = upstream_status


class MenuAnalysisError(Exception):
    """Analysis stopped: EMPTY_MENU_TEXT, PROFILE_DATA_MISSING or
    OUTPUT_PARSING_FAILED."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _menu_extract_cache_key(raw_text, fw_state, headings, menu_source):
    """Çıkarım girdisini oluşturan TÜM bileşenlerden deterministik anahtar üret.
    headings ve menu_source istemi değiştirdiği için anahtara dahildir."""
    h = hashlib.sha256()
    for part in (raw_text, str(fw_state or ""),
                 json.dumps(headings or [], ensure_ascii=False),
                 str(menu_source or "")):
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return MENU_EXTRACT_CACHE_PREFIX + h.hexdigest()


def _menu_scan_cache_key(clean_url, prefix=MENU_SCAN_CACHE_PREFIX):
    """Normalize edilmiş menü URL'inden deterministik cache anahtarı üret.

    ``clean_url`` zaten ``_validate_menu_url``'den tracking-param + fragment
    temizli gelir. Burada YALNIZCA anahtar türetmek için ek normalizasyon:
    scheme+host lowercase, path sonu '/' kırpma, query parametre sıralama —
    böylece trailing-slash / host-case / param-sırası farkları yanlış-miss
    yaratmaz. Fetch edilen gerçek URL değişmez."""
    p = urlparse(clean_url)
    scheme = (p.scheme or "").lower()
    netloc = (p.netloc or "").lower()
    path = p.path.rstrip("/") or "/"
    query = urlencode(sorted(parse_qsl(p.query, keep_blank_values=False)))
    norm = urlunparse((scheme, netloc, path, "", query, ""))
    return prefix + hashlib.sha256(norm.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------

@menu_operation
def acquire_menu(url, *, https_only=False):
    """Fetch and extract one public menu page; returns the scan result dict.

    One call is one LP15-B1 operation: a single 30 s deadline and byte/request
    budget covers the main page, redirects, sub-pages and WordPress fallback.
    `https_only=True` (native intake, which has already refused non-HTTPS
    input) additionally never follows a discovered `http://` sub-page and uses
    its own scan-cache namespace; HTTPS→HTTP redirects are refused by the
    boundary for every caller.
    """
    base_parsed, clean_url, err = _validate_menu_url(url)
    if err:
        raise MenuAcquisitionError("URL_INVALID", err)
    url = clean_url

    if _is_google_drive_url(url):
        current_app.logger.debug(f"[DRIVE] Intercepted Google Drive URL: {loggable_url(url)}")
        drive_result, drive_err = _process_google_drive_url(url)
        if drive_err:
            raise MenuAcquisitionError("DRIVE", drive_err)
        return drive_result

    cache_key = _menu_scan_cache_key(
        url, MENU_SCAN_HTTPS_ONLY_CACHE_PREFIX if https_only else MENU_SCAN_CACHE_PREFIX)
    if redis_client:
        try:
            cached_raw = redis_client.get(cache_key)
        except Exception as e:
            cached_raw = None
            current_app.logger.warning(f"[MENU CACHE] read failed: {type(e).__name__}")
        if cached_raw:
            try:
                cached_result = json.loads(cached_raw)
                cached_result["cached"] = True
                current_app.logger.info(f"[MENU CACHE] HIT — {loggable_url(url)}")
                return cached_result
            except (json.JSONDecodeError, TypeError):
                current_app.logger.warning(f"[MENU CACHE] corrupt entry for {loggable_url(url)} — ignoring")

    try:
        resp = _fetch_page(url)
    except ValueError as e:
        # _resolve_host_safely raises ValueError on a blocked redirect target.
        raise MenuAcquisitionError("FETCH_REJECTED", str(e)) from None
    except http_req.exceptions.Timeout:
        raise MenuAcquisitionError("TIMEOUT") from None
    except http_req.exceptions.RequestException as e:
        upstream = getattr(getattr(e, "response", None), "status_code", None)
        raise MenuAcquisitionError("CONNECTION", type(e).__name__, upstream) from None

    content_type = resp.headers.get("Content-Type", "")
    if "html" not in content_type and "text" not in content_type:
        raise MenuAcquisitionError("UNSUPPORTED_TYPE")

    raw_html = resp.text
    current_app.logger.info(f"[SCRAPER] Page 1 (main) — {loggable_url(url)} — HTTP {resp.status_code} — {len(raw_html)} bytes")

    # Ana sayfa HTML'i TEK KEZ parse edilir ve her tüketiciye aynı soup verilir:
    # framework_state (script tag'leri henüz decompose edilmeden), link keşfi ve
    # bölüm çıkarımı. Eski akış aynı (3 MB'a varan) HTML'i 2-3 kez baştan parse
    # ediyordu — büyük sayfalarda saniyeler mertebesinde saf CPU israfı.
    try:
        soup = bounded_soup(raw_html)
    except ValueError as e:
        raise MenuAcquisitionError("PARSE_LIMIT", str(e)) from None
    framework_state, fw_type = _extract_framework_state(raw_html, soup=soup)
    sub_links = _discover_menu_links(soup, base_parsed)
    if https_only:
        sub_links = [link for link in sub_links if urlparse(link).scheme == "https"]
    current_app.logger.info(f"[SCRAPER] Discovered {len(sub_links)} sub-links, crawling {min(len(sub_links), MAX_SUB_PAGES)}")

    for tag in soup(["script", "style", "iframe", "object", "embed", "link", "meta"]):
        tag.decompose()

    title = soup.title.string.strip()[:256] if soup.title and soup.title.string else ""
    sections = _extract_page_sections(raw_html, soup)

    # At most six subpages share the same fetch/deadline/aggregate budget.
    crawl_errors = []
    targets = sub_links[:MAX_SUB_PAGES]
    if targets:
        app = current_app._get_current_object()

        def _crawl_sub_page(idx, sub_url):
            try:
                sub_resp = _fetch_page(sub_url, timeout=6)
                app.logger.info(f"[SCRAPER] Page {idx+2}/{len(targets)+1} — {loggable_url(sub_url)} — HTTP {sub_resp.status_code}")
                sub_soup = bounded_soup(sub_resp.text)
                for tag in sub_soup(["script", "style", "iframe", "object", "embed", "link", "meta"]):
                    tag.decompose()
                sub_sections = _extract_page_sections(sub_resp.text, sub_soup)
                app.logger.info(f"[SCRAPER] Page {idx+2} → Extracted {len(sub_sections)} section(s)")
                return sub_sections, None
            except Exception as e:
                status = getattr(getattr(e, 'response', None), 'status_code', 'N/A')
                app.logger.warning(f"[SCRAPER] Page {idx+2} FAILED — {loggable_url(sub_url)} — Status: {status} — {type(e).__name__}")
                return [], {"url": sub_url, "error": f"{type(e).__name__}: {status}"}

        # Sequential crawl shares one operation deadline/count/byte authority.
        crawl_results = [_crawl_sub_page(i, target) for i, target in enumerate(targets)]
        for sub_sections, err in crawl_results:
            sections.extend(sub_sections)
            if err:
                crawl_errors.append(err)

    sections = bound_sections(sections)
    all_text_parts = []
    for sec in sections:
        all_text_parts.append(f"[{sec['category']}]\n{sec['text']}")
    body_text = "\n\n".join(all_text_parts)

    content_quality_ok = _content_has_food_items(body_text) if body_text else False

    if not content_quality_ok:
        current_app.logger.info(f"[SCRAPER] Content quality low (no food keywords) — trying WordPress API fallback")
        wp_title, wp_sections = _try_wordpress_api(base_parsed, raw_html)
        if wp_sections:
            sections = wp_sections
            if wp_title:
                title = wp_title
            all_text_parts = [f"[{sec['category']}]\n{sec['text']}" for sec in sections]
            body_text = "\n\n".join(all_text_parts)
            current_app.logger.info(f"[SCRAPER] WordPress API recovered {len(sections)} sections, {len(body_text)} chars")

    if not body_text or len(body_text.strip()) < 20:
        # Ana soup zaten script/style/iframe/... temizli; fallback'in fazladan
        # istediği noscript/svg burada düşürülür. Aynı HTML'i yeniden parse
        # etmekle birebir aynı metni verir, üçüncü tam parse'ı ortadan kaldırır.
        for tag in soup(["noscript", "svg"]):
            tag.decompose()
        body_text = bounded_text(soup)
        current_app.logger.info(f"[SCRAPER] Section extraction empty — used full-body fallback: {len(body_text)} chars")

    if not body_text or len(body_text.strip()) < 20:
        raise MenuAcquisitionError("UNREADABLE")

    body_text = re.sub(r'\s{3,}', '  ', body_text)
    body_text = re.sub(r'(\n\s*){3,}', '\n\n', body_text)

    headings = [sec["category"] for sec in sections if sec["category"] != "Genel"]
    unique_headings = [str(h)[:256] for h in dict.fromkeys(headings)][:40]

    current_app.logger.info(f"[SCRAPER] Total sections: {len(sections)} — Unique categories: {len(unique_headings)}")
    current_app.logger.info(f"[SCRAPER] Raw body_text length: {len(body_text)} chars")

    # Büyük menüler (örn. ~32k karakterlik BigChefs) tek sayfada tüm kategorileri
    # (kahvaltıdan ana yemek/tatlıya) barındırır; 18000'de kesmek sonraki yarıyı
    # (makarna, schnitzel, et, balık) atıyordu. gpt-4o-mini 128k bağlamla bunu
    # rahat işler. Bu sınır, çıkarıcıdaki _MENU_EXTRACT_MAX_CHARS ile hizalı olmalı.
    if len(body_text) > _BODY_TEXT_MAX:
        body_text = body_text[:_BODY_TEXT_MAX]
        current_app.logger.info(f"[SCRAPER] Truncated body_text to {_BODY_TEXT_MAX} chars")

    result = {
        "title": title,
        "headings": unique_headings,
        "body_text": body_text,
        "source_url": url,
        "menu_source": "web_scraper",
        "sub_pages_crawled": len(sub_links[:MAX_SUB_PAGES]),
        "total_sections": len(sections),
        "crawl_errors": crawl_errors if crawl_errors else None,
    }

    if framework_state:
        fw_str = json.dumps(framework_state, ensure_ascii=False)
        if len(fw_str) > 15000:
            fw_str = fw_str[:15000]
        result["framework_state"] = fw_str
        result["framework_type"] = fw_type

    if redis_client:
        try:
            redis_client.setex(cache_key, MENU_SCAN_CACHE_TTL, json.dumps(result, ensure_ascii=False))
            current_app.logger.info(f"[MENU CACHE] STORE — {loggable_url(url)} (ttl={MENU_SCAN_CACHE_TTL}s)")
        except Exception as e:
            current_app.logger.warning(f"[MENU CACHE] store failed: {type(e).__name__}")

    return result


def analysis_request_from_scan(scan):
    """The analysis input a scan result implies — the composition the web
    client has always sent to `/api/menu/analyze` (static/coach_widget.js
    `processMenuUrl`): title + headings + body text as `menu_text`, plus the
    scan's own source, framework state and headings. Server-side, so a native
    caller never relays (and can never substitute) remote menu text."""
    title = scan.get("title") or ""
    headings = scan.get("headings") or []
    menu_text = "\n".join([title, *headings, scan.get("body_text") or ""])
    return {
        "raw_text": menu_text.strip(),
        "fw_state": scan.get("framework_state"),
        "menu_source": scan.get("menu_source") or "web_scraper",
        "headings": headings or None,
    }


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_menu_text(user_id, raw_text, fw_state=None, menu_source="web_scraper",
                      headings=None):
    """Categorize, estimate and score one menu text for `user_id`.

    Returns a dict: `menu_source` (raw token), `categories` (ordered
    {category: [item]}, each list sorted by (-score, name)), `coach_picks`
    (the top three scored items), `remaining`/`target`/`consumed` (rounded
    ints). An item carries the web fields (`name`, `macros`, `score`,
    `warnings`, `reason`, `confidence`, `macro_source`) plus three internal
    ones the web projection drops: `has_macros`, `fit_flags` and
    `fit_warnings` (the canonical locale-free score tokens).

    Reads only (`UserSession`, today's `MealLog`); never writes the diary.
    """
    if not raw_text or len(raw_text.replace(" ", "").replace("\n", "")) < 15:
        raise MenuAnalysisError("EMPTY_MENU_TEXT")

    sess = UserSession.query.filter_by(user_id=user_id)\
        .order_by(UserSession.created_at.desc(), UserSession.id.desc()).first()
    # Hedef makro dagilimi ARTIK burada turetilmez (Sprint 13 PR2, C2): tek
    # kanonik otorite `app.services.nutrition_targets`. Otorite yapilandirilmis
    # hedef yoksa yoklugu (None) doner; bu endpoint onu KENDI mevcut
    # "profil verisi eksik" sozlesmesine cevirir — sayi uydurmaz (F3a).
    targets = derive_daily_macro_targets(
        getattr(sess, "target_calories", None), getattr(sess, "goal", None))
    if targets is None:
        raise MenuAnalysisError("PROFILE_DATA_MISSING")

    today_str = day_key()
    meals = MealLog.query.filter_by(user_id=user_id, tarih=today_str).all()
    consumed = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
    for m in meals:
        consumed["calories"] += m.kalori or 0
        consumed["protein"]  += m.protein or 0
        consumed["carbs"]    += m.karb or 0
        consumed["fat"]      += m.yag or 0

    remaining = remaining_macro_budget(targets, consumed).as_dict()

    headings_hint = headings

    extract_cache_key = _menu_extract_cache_key(raw_text, fw_state, headings_hint, menu_source)
    categorized = None
    if redis_client:
        try:
            cached_extract = redis_client.get(extract_cache_key)
        except Exception as e:
            cached_extract = None
            current_app.logger.warning(f"[EXTRACT CACHE] read failed: {type(e).__name__}")
        if cached_extract:
            try:
                parsed_extract = json.loads(cached_extract)
                if isinstance(parsed_extract, dict) and parsed_extract:
                    categorized = {k: v for k, v in parsed_extract.items() if isinstance(v, list)}
                    current_app.logger.info(f"[EXTRACT CACHE] HIT — {len(categorized)} categories (LLM extraction skipped)")
            except (json.JSONDecodeError, TypeError):
                categorized = None
                current_app.logger.warning("[EXTRACT CACHE] corrupt entry — ignoring")

    if not categorized:
        try:
            categorized = _extract_categorized_items(raw_text, fw_state, headings=headings_hint, menu_source=menu_source)
        except Exception as e:
            current_app.logger.warning(f"[ANALYZE] Extraction crashed: {type(e).__name__}")
            categorized = {}

        # Yeniden deneme yalnızca fw_state İLE denenmişken anlamlıdır: fw_state
        # zaten None ise ikinci çağrı birebir aynı girdiyle (temperature=0) aynı
        # boş sonucu üretir — en pahalı LLM çağrısını sebepsiz ikiye katlıyordu.
        if not categorized and fw_state:
            current_app.logger.info(f"[ANALYZE] First extraction returned empty — retrying without framework_state")
            try:
                categorized = _extract_categorized_items(raw_text, None, headings=headings_hint, menu_source=menu_source)
            except Exception as e:
                current_app.logger.warning(f"[ANALYZE] Retry extraction crashed: {type(e).__name__}")
                categorized = {}

        if categorized and redis_client:
            try:
                redis_client.setex(extract_cache_key, MENU_EXTRACT_CACHE_TTL,
                                   json.dumps(categorized, ensure_ascii=False))
                current_app.logger.info(f"[EXTRACT CACHE] STORE — {len(categorized)} categories (ttl={MENU_EXTRACT_CACHE_TTL}s)")
            except Exception as e:
                current_app.logger.warning(f"[EXTRACT CACHE] store failed: {type(e).__name__}")

    if not categorized:
        current_app.logger.warning(f"[ANALYZE] FAILED: No food items extracted. raw_text length={len(raw_text)}, "
              f"has_food_keywords={_content_has_food_items(raw_text)}")
        raise MenuAnalysisError("OUTPUT_PARSING_FAILED")

    all_items = []
    for cat, items in categorized.items():
        for name in items:
            if isinstance(name, str) and name.strip():
                all_items.append((cat, name.strip()))

    if not all_items:
        raise MenuAnalysisError("OUTPUT_PARSING_FAILED")

    # MAX_MENU_ITEMS ai_nutrition'dan gelir: istem ("toplam en fazla N yemek"),
    # bu kırpma ve LLM çıkış bütçesi (_MENU_EXTRACT_MAX_TOKENS) tek kaynaktan hizalı.
    # Kırpma kategoriler arası ROUND-ROBIN yapılır: düz [:N] kesimi çıkarım
    # sırasındaki son kategorileri (tatlılar, veganlar...) toptan düşürüyordu.
    item_names = list(dict.fromkeys(name for _, name in all_items))
    if len(item_names) > MAX_MENU_ITEMS:
        current_app.logger.info(f"[MACRO ENGINE] Capping items from {len(item_names)} to {MAX_MENU_ITEMS} (round-robin)")
        all_items = _cap_items_round_robin(all_items, MAX_MENU_ITEMS)
        item_names = list(dict.fromkeys(name for _, name in all_items))
    current_app.logger.info(f"[MACRO ENGINE] Starting macro pipeline for {len(item_names)} unique items")

    # Ad → kategori haritası (ilk kategori kazanır) — FatSecret çözümlemesinde
    # belirsiz adları ayırt etmek için ('Margarita' Pizzalar'da → pizza, kokteyl değil).
    category_map = {}
    for cat, name in all_items:
        category_map.setdefault(name, cat)

    # Menünün KENDİ beyan ettiği porsiyon gramajı ('(220 GR)') — evrensel restoran
    # porsiyonu denetiminde otorite ipucu. Çoğu öğede None (gramaj belirtilmemiş).
    stated_grams_map = {n: nutrition_pipeline.parse_stated_grams(n) for n in item_names}

    cached_hits, uncached_names = _get_cached_macros(item_names, basis="per_serving")
    if cached_hits:
        current_app.logger.info(f"[MACRO ENGINE] Cache hit: {len(cached_hits)}/{len(item_names)} items from cache")

    macro_map = dict(cached_hits)
    # Kaynak izleme: her öğenin makrosunun NEREDEN çözüldüğü, yanıttaki
    # confidence/macro_source alanlarına çevrilir (_MACRO_CONFIDENCE).
    source_map = {n: "cache" for n in cached_hits}
    per_100g_items = {}
    lookup_names = uncached_names
    if not lookup_names:
        current_app.logger.info(f"[MACRO ENGINE] All {len(item_names)} items served from cache — skipping FatSecret + LLM")
    else:
        try:
            token = _get_fatsecret_token()
            current_app.logger.info(f"[MACRO ENGINE] FatSecret token acquired")
            per_serving, per_100g_items = _lookup_macros_fatsecret(lookup_names, token, category_map)
            macro_map.update(per_serving)
            for n in per_serving:
                source_map[n] = "fatsecret_serving"
        except Exception as e:
            current_app.logger.warning(f"[MACRO ENGINE] FatSecret FAILED — uncached items will use LLM fallback: {type(e).__name__}")

    # Kalan iki LLM aşaması BİRBİRİNDEN BAĞIMSIZDIR: (a) per-100g öğelerin porsiyon
    # ağırlığı tahmini, (b) hiçbir kaynaktan çözülemeyen öğelerin makro tahmini.
    # (b) kümesi (a)'nın sonucuna bağlı değildir — per-100g öğeler ölçeklemeyle her
    # durumda çözülür — bu yüzden iki çağrı eşzamanlı koşar; süre max(a,b) olur
    # (eskiden a+b: iki ağır LLM turu art arda bekleniyordu).
    missing = [n for n in lookup_names if n not in macro_map and n not in per_100g_items]
    current_app.logger.info(f"[MACRO ENGINE] After FatSecret: {len(macro_map)} resolved, "
                            f"{len(per_100g_items)} per-100g to scale, {len(missing)} missing → LLM fallback")

    fallback_g = {}
    if per_100g_items:
        # Tur-bazli gram yedegi: LLM tahmini yok/aralik-disiyken duz 150 g yerine
        # yemek-turu varsayilani (makarna/burger 300-400 g) kullanilir — duz 150 g
        # buyuk porsiyonlu turleri yariya indiriyordu (porsiyon-eslesme hatasi #3).
        fallback_g = {
            n: nutrition_pipeline.DISH_SERVING_DEFAULT_G.get(
                _primary_dish_type(n, category_map.get(n)), 150.0)
            for n in per_100g_items
        }

    serving_weights, weight_fallbacks = {}, set()
    llm_macros = {}
    if per_100g_items and missing:
        app = current_app._get_current_object()

        # Executor threads: bind the requesting account (spend guard).
        @ai_spend_guard.bind_subject
        def _weights_job():
            with app.app_context():
                return _estimate_serving_weights_llm(list(per_100g_items.keys()),
                                                     fallback_weights=fallback_g,
                                                     return_fallbacks=True)

        @ai_spend_guard.bind_subject
        def _macros_job():
            # Kategori bağlamı LLM'e de geçer: 'Margarita'@Pizzalar kokteyl değil
            # tek kişilik pizza olarak tahmin edilsin (tür referanslı prompt).
            with app.app_context():
                return _estimate_macros_llm(missing, category_map)

        with ThreadPoolExecutor(max_workers=2) as ex:
            weights_fut = ex.submit(_weights_job)
            macros_fut = ex.submit(_macros_job)
            serving_weights, weight_fallbacks = weights_fut.result()
            llm_macros = macros_fut.result()
    elif per_100g_items:
        serving_weights, weight_fallbacks = _estimate_serving_weights_llm(
            list(per_100g_items.keys()), fallback_weights=fallback_g, return_fallbacks=True)
    elif missing:
        llm_macros = _estimate_macros_llm(missing, category_map)

    for name, base_macros in per_100g_items.items():
        grams = serving_weights.get(name, fallback_g.get(name, 150.0))
        scale = grams / 100.0
        scaled = {
            "calories": round(base_macros["calories"] * scale, 1),
            "protein": round(base_macros["protein"] * scale, 1),
            "carbs": round(base_macros["carbs"] * scale, 1),
            "fat": round(base_macros["fat"] * scale, 1),
        }
        macro_map[name] = scaled
        source_map[name] = ("fatsecret_scaled_fallback" if name in weight_fallbacks
                            else "fatsecret_scaled")
        current_app.logger.info("[MACRO ENGINE] Scaled per-100g→serving: 1 item")

    if llm_macros:
        macro_map.update(llm_macros)
        for n in llm_macros:
            source_map[n] = "llm"

    # Evrensel-porsiyon düzeltmesi: menünün KENDİ beyan ettiği gramaja sahip ama
    # kalorisi imkânsız-düşük (örn. 220g Tavuklu Fajita → 125 kcal) ya da hiç veri
    # gelmemiş (Portakallı Izgara Somon → 0 kcal) öğeleri, gramajı LLM'e OTORİTE
    # ipucu vererek gerçekçi tek-porsiyona yeniden tahmin ettir. Yalnızca beyan
    # gramajı olan öğeler; eksik tahmini DÜZELTİR, gramajsız meşru düşükleri bozmaz.
    reestimate = [
        n for n in item_names
        if stated_grams_map.get(n)
        and (macro_map.get(n, {}).get("calories", 0) <= 0
             or nutrition_pipeline.is_low_for_stated_grams(macro_map.get(n), stated_grams_map[n]))
    ]
    if reestimate:
        current_app.logger.info(f"[MACRO ENGINE] Re-estimating {len(reestimate)} stated-gram low/zero items")
        grams_hint = {n: stated_grams_map[n] for n in reestimate}
        re_macros = _estimate_macros_llm(reestimate, category_map, grams_hint=grams_hint)
        for n, m in re_macros.items():
            # Yeni değer yalnızca gerçekten daha makulse (pozitif VE artık düşük-yoğunluk
            # değil) uygulanır; aksi halde özgün değer korunur (asla kötüleştirme).
            if m.get("calories", 0) > 0 and not nutrition_pipeline.is_low_for_stated_grams(m, stated_grams_map[n]):
                current_app.logger.info("[MACRO ENGINE] Re-estimated: 1 item")
                macro_map[n] = m
                source_map[n] = "llm_stated_grams"

    _cache_macros(macro_map, basis="per_serving")

    final_resolved = sum(1 for n in item_names if n in macro_map and macro_map[n].get("calories", 0) > 0)
    final_zero = len(item_names) - final_resolved
    current_app.logger.info(f"[MACRO ENGINE] Final pipeline result: {final_resolved}/{len(item_names)} items have non-zero macros"
          + (f" — WARNING: {final_zero} items still at 0" if final_zero else ""))

    categories_result = {}
    all_scored = []

    for cat, name in all_items:
        macros = macro_map.get(name)
        has_macros = macros is not None and macros.get("calories", 0) > 0
        macro_source = source_map.get(name, "none") if has_macros else "none"
        confidence = _MACRO_CONFIDENCE.get(macro_source, 0.0)

        # Deterministik saglik/biyoloji kontrolu: termodinamik olarak imkansiz
        # girdileri (tek porsiyona >3000 kcal / >300 g makro, kalori-makro enerji
        # ihlali) skor verilip yazdirilmadan ONCE yanit dizisinden TAMAMEN ELE.
        if has_macros:
            valid, _flags, reasons = nutrition_pipeline.check_serving({
                "metric_serving_amount": 0,
                "calories": macros.get("calories", 0),
                "protein": macros.get("protein", 0),
                "carbs": macros.get("carbs", 0),
                "fat": macros.get("fat", 0),
            })
            # Menü-özel: absürt düşük kalorili "yemek" (≈başarısız eşleşme) → ele.
            if valid and nutrition_pipeline.is_implausibly_low_menu_kcal(macros):
                current_app.logger.info("[MACRO ENGINE] DISCARDED implausibly-low dish: 1 item")
                valid = False
                reasons = ["menu_calories_implausibly_low"]
            if not valid:
                current_app.logger.info("[MACRO ENGINE] DISCARDED implausible item: 1 item")
                continue

            # Porsiyon bandi — ZORLAYICI (yalniz ust yonde): tur kesinse ve deger
            # bant USTUYSE tum makrolar oransal olarak bant ustune kirpilir
            # (saha vakasi: Margarita pizza 1320 kcal — cache/FatSecret/LLM hangi
            # kaynaktan sizarsa sizsin tek bogum noktasi burasi). Bant ALTI yalniz
            # loglanir: 300 kcal cocuk burgeri mesru olabilir, kaynak-tarafi
            # kapilar (gate_per_serving) sistematik dusukleri zaten ceviriyor.
            dish_type = _primary_dish_type(name, cat)
            clamped, changed = nutrition_pipeline.clamp_to_band(macros, dish_type)
            if changed:
                current_app.logger.info("[MACRO ENGINE] PORTION BAND CLAMP: 1 item")
                macros = clamped
                # Kaynak değeri bant-üstüydü ve kırpıldı → kaynağın güveni artık
                # geçerli değil; kırpılmış tahmin düşük-güvenli raporlanır.
                confidence = min(confidence, 0.5)
            elif nutrition_pipeline.check_portion_band(macros.get("calories", 0), dish_type) == "low":
                current_app.logger.info("[MACRO ENGINE] PORTION BAND LOW: 1 item")

        if not has_macros:
            macros = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
            current_app.logger.info("[MACRO ENGINE] ZERO-MACRO ITEM: 1 item — no data from FatSecret or LLM")

        if has_macros:
            score, warnings, reason = _menu_score(macros, remaining)
            # The same canonical score, as locale-free tokens for non-web callers.
            fit = nutrition_pipeline.score_compatibility(macros, remaining)
            fit_flags, fit_warnings = list(fit["flags"]), list(fit["warnings"])
        else:
            score, warnings, reason = 0, [], t("route.macros_unavailable")
            fit_flags, fit_warnings = [], []

        item_obj = {
            "name": name,
            "macros": {k: int(round(v)) for k, v in macros.items()},
            "score": score,
            "warnings": warnings,
            "reason": reason,
            # Ekleme (additive) alanlar: makronun kaynağı ve 0-1 güven skoru.
            # Mevcut istemciler bilmediği alanları yok sayar.
            "confidence": round(confidence, 2),
            "macro_source": macro_source,
            # Internal (dropped by the web projection).
            "has_macros": has_macros,
            "fit_flags": fit_flags,
            "fit_warnings": fit_warnings,
        }

        if cat not in categories_result:
            categories_result[cat] = []
        categories_result[cat].append(item_obj)
        if has_macros:
            all_scored.append(item_obj)

    for cat in categories_result:
        categories_result[cat].sort(key=lambda x: (-x["score"], x["name"]))

    all_scored.sort(key=lambda x: (-x["score"], x["name"]))

    return {
        "menu_source": menu_source,
        "categories": categories_result,
        "coach_picks": all_scored[:3],
        "remaining": {k: int(round(v)) for k, v in remaining.items()},
        "target": {k: int(round(v)) for k, v in targets.as_dict().items()},
        "consumed": {k: int(round(v)) for k, v in consumed.items()},
    }


_WEB_ITEM_KEYS = ("name", "macros", "score", "warnings", "reason",
                  "confidence", "macro_source")


def web_analysis_payload(analysis):
    """The historical `/api/menu/analyze` success body."""
    def web_item(item):
        return {key: item[key] for key in _WEB_ITEM_KEYS}

    categories = {cat: [web_item(i) for i in items]
                  for cat, items in analysis["categories"].items()}
    menu_source = analysis["menu_source"]
    source_label = {"google_drive": "Google Drive", "web_scraper": "Web Scraper"}.get(menu_source, menu_source)
    return {
        "success": True,
        "menu_source": source_label,
        "coach_picks": [web_item(i) for i in analysis["coach_picks"]],
        "categories": categories,
        "items": [item for items in categories.values() for item in items],
        "remaining": analysis["remaining"],
        "target": analysis["target"],
        "consumed": analysis["consumed"],
    }
