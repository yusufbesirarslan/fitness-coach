"""MealLog defteri: foto/AI makro tahmini, bugün/geçmiş, değerlendirme.

app/blueprints/nutrition.py (god-module) eş-anlamlı parçalara bölündü; rotalar
ve davranış AYNI (aynı `nutrition` blueprint'i, aynı endpoint adları). Ortak
`bp` paketten gelir.
"""
import json
import s3_helper
from flask import Response, current_app, jsonify, request
from flask_login import current_user
from app.auth_middleware import require_auth

from app.blueprints.nutrition import bp
from app.config import AI_RATELIMIT
from app.extensions import _user_or_ip_key, auth_write_limit, db, limiter
from app.i18n import current_locale, t
from app.models import MealLog, UserSession
from app.prompts import nutrition as nutrition_prompts
from app.services.ai import _openai_chat
from app.services.nutrition_pipeline import sanitize_meal_total_macros
from app.services.nutrition_targets import (
    derive_daily_macro_targets,
    remaining_macro_budget,
)
from app.services.gamification import complete_quest_for_user
from app.services import meal_idempotency, mobile_diary_mutation, mobile_log_food
from app.services.mobile_nutrition.serialization import SLOT_BY_MEAL_LABEL
from app.services.validators import _meal_photo_url, validate_meal_photo
from app.timeutil import day_key, display_ddmm


def _web_daily_macros(macros):
    """Project a canonical DailyMacros onto the web payload's Turkish keys.

    Absence stays ``None`` — never a zero-filled object that would read as a
    configured target of nothing (F3a). Rounding is the browser's job.
    """
    if macros is None:
        return None
    return {
        "kalori": macros.calories,
        "protein": macros.protein,
        "karb": macros.carbs,
        "yag": macros.fat,
    }


# F7 / C12 (Sprint 13 PR3). `MealLog.ogun` bir GÖRÜNTÜ etiketi kolonudur ve
# bilerek etiket yazan başka yazarlar da vardır ("AI Koç", "<gönderen>…alınan
# öneri"); kolon enum'a çevrilmez. Doğrulama TRANSPORT sınırına aittir: web
# /meal-log yalnızca dört kanonik öğün etiketini kabul eder. Serbest metin
# daha önce deftere sızıyor, mobil projeksiyonda `unknown` slot'a düşüyor ve
# 100 karakteri aşınca PostgreSQL'de DataError veriyordu.
WEB_MEAL_SLOTS = tuple(SLOT_BY_MEAL_LABEL)


def _provider_command(ogun, provider_food):
    """Adapt the web transport into the canonical semantic LogFood command.

    Yalnızca KİMLİKLER taşınır (sağlayıcı, besin, porsiyon, adet). Makro
    hesabı sunucuda, `mobile_log_food` içinde sağlayıcı gerçeğinden yeniden
    yapılır — tarayıcı önizlemesi ASLA kalıcı otorite değildir (C3/F5).
    """
    if not isinstance(provider_food, dict):
        raise mobile_log_food.InvalidLogFoodCommand("provider command must be an object")
    return mobile_log_food.parse_command({
        "kind": "provider_backed",
        "provider": provider_food.get("provider", "fatsecret"),
        "food_id": provider_food.get("food_id"),
        "serving_id": provider_food.get("serving_id"),
        "quantity": provider_food.get("quantity", 1),
        "slot": SLOT_BY_MEAL_LABEL[ogun],
        "discovery_source": provider_food.get("discovery_source", "search"),
    })


@bp.route("/meal-log", methods=["POST"])
@require_auth
@limiter.limit(AI_RATELIMIT, key_func=_user_or_ip_key)
def log_meal():
    data = request.get_json(silent=True) or {}
    ogun     = data.get("ogun", "")
    yemekler = data.get("yemekler", "")

    # F7: slot doğrulaması HER yan etkiden önce — foto yükleme, sağlayıcı turu,
    # LLM çağrısı, defter yazımı ve görev mutasyonu bu kapının ARDINDA kalır.
    if ogun not in WEB_MEAL_SLOTS:
        return jsonify({"error": t("route.invalid_meal_slot")}), 400

    provider_food = data.get("provider_food")
    if provider_food is not None:
        return _log_provider_backed_meal(data, ogun, provider_food)

    if not yemekler:
        return jsonify({"error": t("route.meal_foods_required")}), 400

    # ELLE girilen beslenme kullanıcı-otoriterdir ve öyle kalır (C3) — ama tek
    # bir tipli sınır politikasıyla: kanonik `ManualNutritionSnapshot` (mobil
    # manuel komutun AYNI doğrulayıcısı). Eskiden `_to_float(..., 0)` bozuk /
    # eksik / NaN / negatif değerleri SESSİZCE 0'a çeviriyordu; sıfır bir ölçüm
    # değildir, 400 doğru cevaptır (PR3 §11). Doğrulama foto yüklemesinden
    # ÖNCE — reddedilen bir istek S3'te yetim nesne bırakmasın.
    override = data.get("override_macros")
    manual_nutrition = None
    if override is not None:
        try:
            manual_nutrition = mobile_log_food.parse_manual_nutrition({
                "energy_kcal": override.get("kalori"),
                "protein_g": override.get("protein"),
                "carbohydrate_g": override.get("karb"),
                "fat_g": override.get("yag"),
            } if isinstance(override, dict) else override)
        except mobile_log_food.InvalidLogFoodCommand:
            return jsonify({"error": t("route.invalid_manual_nutrition")}), 400

    idempotency_key = meal_idempotency.read_idempotency_key()
    existing = meal_idempotency.find_existing(current_user.id, idempotency_key)
    if existing:
        response = {
            "message": t("route.x_logged", name=existing.ogun),
            "nutrients": {
                "kalori": existing.kalori,
                "protein": existing.protein,
                "karb": existing.karb,
                "yag": existing.yag,
            },
        }
        if existing.photo_key:
            response["photo_url"] = _meal_photo_url(existing)
        return jsonify(response)

    # Opsiyonel öğün fotoğrafı: doğrula ve (varsa) S3'e yükle. S3 hatası öğün
    # kaydını bloklamaz (fail-open) — yalnızca foto atlanır.
    photo_bytes, photo_mime, photo_err = validate_meal_photo(data.get("image"))
    if photo_err:
        return jsonify({"error": photo_err}), 400
    meal_photo_key = None
    if photo_bytes:
        try:
            if s3_helper.is_enabled():
                meal_photo_key = s3_helper.upload_image(
                    photo_bytes, content_type=photo_mime,
                    prefix="meals", user_id=current_user.id,
                )
        except Exception as e:
            current_app.logger.info(f"[S3] Öğün fotoğrafı yüklemesi başarısız: {type(e).__name__}: {e}")

    _FITNESS_DICT = {
        r'(?i)\b(\d+)\s*(?:ölçek|scoop)\s*(?:whey|protein\s*tozu|protein\s*powder)':
            lambda m: (f"{m.group(1)} ölçek whey protein tozu ({int(m.group(1))*30}g)", None),
        r'(?i)\b(\d+)\s*(?:ölçek|scoop)\s*kreatin':
            lambda m: (f"{m.group(1)} ölçek kreatin ({int(m.group(1))*5}g)", None),
        r'(?i)\b(\d+)\s*(?:ölçek|scoop)\s*(?:kazein|casein)':
            lambda m: (f"{m.group(1)} ölçek kazein protein ({int(m.group(1))*33}g)", None),
        r'(?i)\b(\d+)\s*(?:adet\s+)?pirinç\s*patlağı':
            lambda m: (f"{m.group(1)} adet pirinç patlağı ({int(m.group(1))*8}g)", None),
        r'(?i)\bprotein\s*bar[ıi]?\b':
            lambda m: ("1 protein bar (60g)", None),
        r'(?i)\b(\d+)\s*(?:kaşık|tbsp)\s*fıstık\s*ezmesi':
            lambda m: (f"{m.group(1)} yemek kaşığı fıstık ezmesi ({int(m.group(1))*15}g)", None),
        r'(?i)\b(\d+)\s*(?:kaşık|tbsp)\s*(?:bal|honey)':
            lambda m: (f"{m.group(1)} yemek kaşığı bal ({int(m.group(1))*21}g)", None),
        r'(?i)\bbcaa\b':
            lambda m: ("1 ölçek BCAA (7g)", None),
    }
    import re as _re
    normalized_yemekler = yemekler
    for pattern, handler in _FITNESS_DICT.items():
        match = _re.search(pattern, normalized_yemekler)
        if match:
            replacement, _ = handler(match)
            normalized_yemekler = _re.sub(pattern, replacement, normalized_yemekler, count=1)
    if normalized_yemekler != yemekler:
        current_app.logger.info("[MEAL] Fitness shorthand normalized")
    yemekler_for_prompt = normalized_yemekler

    if manual_nutrition is not None:
        # Tipli sınırlar (yukarıda doğrulandı) DB CHECK'iyle aynıdır
        # (100000/50000); fiziksel-sağlık kapısı bunun ÜSTÜNE gelen mevcut tek
        # kaynaktır (menü/koç/diary ile aynı) — "99999 kcal" çöpü deftere
        # sızmasın (C1).
        from app.services import nutrition_pipeline as _np
        kalori, protein, karb, yag = _np.clamp_serving_macros(
            round(float(manual_nutrition.energy_kcal), 1),
            round(float(manual_nutrition.protein_g), 1),
            round(float(manual_nutrition.carbohydrate_g), 1),
            round(float(manual_nutrition.fat_g), 1),
        )
        nutrients = {"kalori": kalori, "protein": protein, "karb": karb, "yag": yag}
        today = day_key()
        entry = MealLog(
            user_id=current_user.id, ogun=ogun, yemekler=yemekler,
            kalori=nutrients["kalori"], protein=nutrients["protein"],
            karb=nutrients["karb"], yag=nutrients["yag"], tarih=today,
            photo_key=meal_photo_key
        )
        entry, created = meal_idempotency.commit_once(entry, idempotency_key)
        # C5: override yolu da AI-hesaplı yolla aynı 'meal_logged' görevini
        # vermeli — kullanıcı makroyu elle girdi diye günlük görev/XP atlanmasın.
        quest_result = complete_quest_for_user(current_user.id, "meal_logged") if created else None
        resp = {"message": t("route.x_logged", name=ogun), "nutrients": nutrients}
        if meal_photo_key:
            resp["photo_url"] = _meal_photo_url(entry)
        if quest_result:
            resp["quest_awarded"] = quest_result
        return jsonify(resp)

    # İstem şablonu app/prompts/nutrition.py'de (WS4).
    prompt = nutrition_prompts.build_meal_totals_prompt(yemekler_for_prompt)

    nutrients = {"kalori": 0, "protein": 0, "karb": 0, "yag": 0}
    raw = ""
    parsed_ok = False

    try:
        raw = _openai_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=nutrition_prompts.MEAL_TOTALS_SYSTEM,
            max_tokens=150,
            temperature=0.0,
        ).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()

        # Bazen AI fazladan metin ekliyor, sadece { } arasını al
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]

        nutrients = json.loads(raw)
        kalori, protein, karb, yag = sanitize_meal_total_macros(
            nutrients.get("kalori"),
            nutrients.get("protein"),
            nutrients.get("karb"),
            nutrients.get("yag"),
        )
        nutrients = {
            "kalori": kalori,
            "protein": protein,
            "karb": karb,
            "yag": yag,
        }
        parsed_ok = True
    except Exception as e:
        current_app.logger.info("MEAL LOG ERROR: %s", type(e).__name__)

    # AI çağrısı/JSON parse başarısızsa nutrients tümü 0 kalır; bunu kanonik
    # MealLog defterine YAZMA — kalıcı sıfır-makro satırı günlük toplamları,
    # protein nudge'ını ve haftalık raporları bozar (sessizce). Hatayı kullanıcıya
    # döndür ki tekrar denesin; commit etme.
    if not parsed_ok:
        return jsonify({
            "error": t("route.meal_calc_failed")
        }), 502

    today = day_key()

    entry = MealLog(
        user_id  = current_user.id,
        ogun     = ogun,
        yemekler = yemekler,
        kalori   = nutrients.get("kalori", 0),
        protein  = nutrients.get("protein", 0),
        karb     = nutrients.get("karb", 0),
        yag      = nutrients.get("yag", 0),
        tarih    = today,
        photo_key = meal_photo_key
    )
    entry, created = meal_idempotency.commit_once(entry, idempotency_key)

    quest_result = complete_quest_for_user(current_user.id, "meal_logged") if created else None
    response = {
        "message": t("route.x_logged", name=ogun),
        "nutrients": nutrients,
    }
    if meal_photo_key:
        response["photo_url"] = _meal_photo_url(entry)
    if quest_result:
        response["quest_awarded"] = quest_result
    return jsonify(response)


def _log_provider_backed_meal(data, ogun, provider_food):
    """Web'in sağlayıcı-destekli öğün yazma yolu — KANONİK LogFood otoritesi.

    Bu yol makro HESAPLAMAZ: `mobile_log_food.log_food` porsiyon gerçeğini
    `mobile_food_discovery.servings(food_id)`'den yeniden çeker ve adetle
    sunucuda ölçekler. Mobil `POST /api/v1/nutrition/logs` ile AYNI servis,
    aynı semantik parmak izi, aynı replay kuralı (C3 → N2/N8). Transport
    ayrıdır; otorite tektir.
    """
    from app.services.ai_gate import BlockingConcurrencyLimit

    # Karışık komut = belirsiz komut. Sağlayıcı kimliği taşıyan bir istek
    # AYRICA makro/foto/serbest açıklama taşıyorsa hangisinin kalıcı olacağını
    # SESSİZCE seçmek yerine kapalı düş (PR3 §7/§33).
    if data.get("override_macros") or data.get("image") or data.get("yemekler"):
        return jsonify({"error": t("route.mixed_meal_command")}), 400

    idempotency_key = meal_idempotency.read_idempotency_key()
    if idempotency_key is None:
        return jsonify({"error": t("route.meal_idempotency_key_required")}), 400

    try:
        command = _provider_command(ogun, provider_food)
        entry, created = mobile_log_food.log_food(
            current_user.id, idempotency_key, command)
    except mobile_log_food.InvalidLogFoodCommand:
        return jsonify({"error": t("route.invalid_provider_meal_command")}), 400
    except mobile_log_food.ProviderFoodNotFound:
        return jsonify({"error": t("route.food_not_found")}), 404
    except mobile_log_food.IdempotencyConflict:
        return jsonify({"error": t("route.meal_idempotency_conflict")}), 409
    except BlockingConcurrencyLimit:
        current_app.logger.warning(
            "log_meal event=blocking_capacity_exhausted stage=provider")
        response = jsonify({"error": t("error.ai_busy")})
        response.status_code = 503
        response.headers["Retry-After"] = "15"
        return response

    # Görev/XP YALNIZCA gerçekten satır oluştuğunda — replay ikinci kez ödül
    # vermez (N8).
    quest_result = complete_quest_for_user(
        current_user.id, "meal_logged") if created else None
    response = {
        "message": t("route.x_logged", name=entry.ogun),
        "nutrients": {
            "kalori": entry.kalori, "protein": entry.protein,
            "karb": entry.karb, "yag": entry.yag,
        },
    }
    if quest_result:
        response["quest_awarded"] = quest_result
    return jsonify(response)


@bp.route("/meal-log/today")
@require_auth
def today_meals():
    """KANONİK 'bugün yenenler' defteri ve toplamları (MealLog — tek doğru kaynak).

    Ana sayfa/beslenme halkaları (static/today.js, static/nutrition.js
    loadTodayData) günlük kalori/makro toplamı için YALNIZCA bunu kullanır.
    DİKKAT: /api/diary/today toplamlarıyla TOPLANMAZ — diary'de 'kaydedilmiş'
    (is_logged) öğünler zaten buraya MealLog satırı olarak yazılır; iki yüzeyi
    toplamak çift sayım olur (bkz. diary_today docstring)."""
    today = day_key()
    meals = MealLog.query.filter_by(user_id=current_user.id, tarih=today)\
        .order_by(MealLog.created_at.asc()).all()

    secret = current_app.config["SECRET_KEY"]
    result = []
    totals = {"kalori": 0, "protein": 0, "karb": 0, "yag": 0}
    for m in meals:
        # F1/N9 (PR4). To correct a row the browser needs an addressable
        # identity and a precondition — and nothing else. Both come from the ONE
        # canonical projection (opaque, owner-bound): never `MealLog.id`, never a
        # raw revision column, never the storage key. Published on the
        # CURRENT-DAY surface only; /meal-log/history stays uncorrectable by
        # construction, which is how N9 remains scoped to today.
        entry_token, revision = mobile_diary_mutation.entry_identity(m, secret)
        result.append({
            "ogun": m.ogun,
            "yemekler": m.yemekler,
            "kalori": m.kalori,
            "protein": m.protein,
            "karb": m.karb,
            "yag": m.yag,
            "source": getattr(m, "source", "manual") or "manual",
            "photo_url": _meal_photo_url(m),
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "entry_token": entry_token,
            "revision": revision,
            "has_photo": bool(m.photo_key),
        })
        totals["kalori"]  += m.kalori or 0
        totals["protein"] += m.protein or 0
        totals["karb"]    += m.karb or 0
        totals["yag"]     += m.yag or 0

    # Sprint 13 PR5 / N4: the browser presents this derivation or none at all.
    # It must not invent a 30/40/30 split, a 2000 kcal stand-in, or a remaining
    # budget of its own. Absence is a real domain state.
    last = UserSession.query.filter_by(user_id=current_user.id)\
        .order_by(UserSession.created_at.desc()).first()
    configured = derive_daily_macro_targets(
        getattr(last, "target_calories", None),
        getattr(last, "goal", None),
    )
    remaining = remaining_macro_budget(configured, {
        "calories": totals["kalori"],
        "protein": totals["protein"],
        "carbs": totals["karb"],
        "fat": totals["yag"],
    })
    return jsonify({
        "meals": result,
        "totals": totals,
        "tarih": display_ddmm(today),
        "targets": _web_daily_macros(configured),
        "remaining": _web_daily_macros(remaining),
    })


@bp.route("/meal-log/entry/<entry_token>", methods=["DELETE"])
@require_auth
@auth_write_limit
def delete_today_meal(entry_token):
    """F1/N9: the web correction primitive — CURRENT-DAY HARD DELETE.

    A thin transport over the canonical mutation authority. It owns the HTTP
    shape and nothing else: ownership, the day boundary, the row lock, the
    revision comparison under that lock and the stored-object lifecycle all
    live in `mobile_diary_mutation` (shared with the native client, whose
    contract is unchanged). This route deliberately holds no delete of its own.

    The day comes from `day_key()` — the server's Istanbul day. A query string,
    a client date and a browser timezone all get no vote, so a valid token for
    an entry outside today addresses nothing and this never becomes a
    historical ledger-management API.

    Sprint 13 chose deletion as the correction primitive (C4, C5): there is no
    slot move, no macro edit and no quantity correction here, and deletion is
    lossy — the browser confirmation says so before it calls.

    Statuses: 204 done · 428 precondition required · 400 malformed precondition
    · 404 absent / not owned / not today · 412 stale · 409 the row's stored
    object is unreleasable (fail closed) · 503 the correction committed but the
    photo release has not finished yet (durably pending, and retryable).
    """
    try:
        revision = mobile_diary_mutation.parse_if_match(
            request.headers.get("If-Match"))
    except mobile_diary_mutation.MissingPrecondition:
        return jsonify({"error": t("route.meal_delete_precondition_required")}), 428
    except mobile_diary_mutation.InvalidPrecondition:
        return jsonify({"error": t("route.meal_delete_precondition_invalid")}), 400

    try:
        mobile_diary_mutation.delete_entry(
            current_user.id,
            day_key(),
            entry_token,
            revision,
            current_app.config["SECRET_KEY"],
        )
    except mobile_diary_mutation.EntryNotFound:
        db.session.rollback()
        # Deliberately identical for "never existed", "belongs to someone else"
        # and "not today" — the response must not become an existence oracle.
        return jsonify({"error": t("route.meal_not_found")}), 404
    except mobile_diary_mutation.StaleDiaryEntry:
        db.session.rollback()
        return jsonify({"error": t("route.meal_delete_stale")}), 412
    except mobile_diary_mutation.UnreleasableStoredObject:
        db.session.rollback()
        return jsonify({"error": t("route.meal_delete_photo_unreleasable")}), 409
    except mobile_diary_mutation.StoredObjectNotReleased:
        # The row IS deleted and that is durable, so a 204 would hide an
        # unfinished lifecycle — the shape of F14 — and a 500 would imply the
        # correction did not happen. What is true is narrower: the ledger
        # correction committed and the photo release is still PENDING, durably,
        # with the exact object key recorded. Repeating this exact request
        # retries that release and converges, and the operator drain converges
        # without the user, so `retryable` here is a fact rather than a hope.
        # The body says the entry is gone; the browser re-reads canonical state
        # either way and never sees it come back.
        return jsonify({
            "error": t("route.meal_delete_photo_not_released"),
            "entry_deleted": True,
            "photo_cleanup": "pending",
            "retryable": True,
        }), 503
    except Exception as error:
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.error(
            "nutrition event=meal_delete_failed error_type=%s",
            type(error).__name__)
        return jsonify({"error": t("route.meal_delete_failed")}), 500
    return "", 204


@bp.route("/meal-log/history")
@require_auth
def meal_history():
    # Önce en yeni N GÜN'ün anahtarlarını al, sonra YALNIZCA o günlerin TÜM
    # satırlarını çek. Eski "ilk 50 satır çek → sonra güne göre grupla" yaklaşımı,
    # bir gün 50-satır sınırına denk geldiğinde o günün toplamını yalnızca sınır
    # içindeki satırlardan hesaplayıp kalori/makroyu sessizce DÜŞÜK gösteriyordu
    # (M2). Gün-sayısıyla sınırlamak her gösterilen günün tam toplamını garanti eder.
    HISTORY_DAYS = 14
    recent_days = [r[0] for r in db.session.query(MealLog.tarih)
        .filter(MealLog.user_id == current_user.id)
        .group_by(MealLog.tarih)
        .order_by(MealLog.tarih.desc())
        .limit(HISTORY_DAYS).all()]

    meals = (MealLog.query.filter(
        MealLog.user_id == current_user.id,
        MealLog.tarih.in_(recent_days),
    ).order_by(MealLog.created_at.desc()).all() if recent_days else [])

    days = {}
    for m in meals:
        if m.tarih not in days:
            days[m.tarih] = {"meals": [], "totals": {"kalori": 0, "protein": 0, "karb": 0, "yag": 0}}
        days[m.tarih]["meals"].append({
            "ogun": m.ogun,
            "yemekler": m.yemekler,
            "kalori": m.kalori,
            "protein": m.protein,
            "karb": m.karb,
            "yag": m.yag,
            "photo_url": _meal_photo_url(m)
        })
        days[m.tarih]["totals"]["kalori"]  += m.kalori or 0
        days[m.tarih]["totals"]["protein"] += m.protein or 0
        days[m.tarih]["totals"]["karb"]    += m.karb or 0
        days[m.tarih]["totals"]["yag"]     += m.yag or 0

    result = [{"tarih": display_ddmm(k), **v} for k, v in days.items()]
    return Response(json.dumps(result, ensure_ascii=False), mimetype="application/json")


@bp.route("/meal-log/review", methods=["POST"])
@require_auth
@limiter.limit(AI_RATELIMIT, key_func=_user_or_ip_key)
def review_meals():
    today = day_key()
    meals = MealLog.query.filter_by(user_id=current_user.id, tarih=today).all()

    if not meals:
        return jsonify({"error": t("route.no_meals_today")}), 400

    last_session = UserSession.query.filter_by(user_id=current_user.id)\
        .order_by(UserSession.created_at.desc()).first()

    # F3b vs F3a (Sprint 13 PR2, §15). Bu route'un `2000`'i PR1'de "yalnızca
    # LLM istemi içinde niteliksel metin üreten iç yedek" diye sınıflandırıldı
    # ve KAPSAM DIŞI bırakıldı. O sınıflandırma istem için DOĞRU, yanıt için
    # DEĞİLDİ: `target` aşağıda JSON payload'ında da YAYIMLANIYOR, yani
    # yapılandırılmamış kullanıcı için uydurma bir "yapılandırılmış hedef"
    # yayımlanıyordu — F3a'nın barkod'da kapatılan deseninin AYNISI.
    # Bu yüzden YALNIZCA yayımlanan alan kanonik otoriteye bağlanır (hedef
    # yoksa `null`); istemin niteliksel yedeği (F3b) BİLEREK DEĞİŞMEDİ.
    configured = derive_daily_macro_targets(
        getattr(last_session, "target_calories", None),
        getattr(last_session, "goal", None))
    target = last_session.target_calories if last_session else 2000
    goal   = last_session.goal if last_session else "genel sağlık"

    meals_text = ""
    total_cal = 0
    for m in meals:
        meals_text += f"- {m.ogun}: {m.yemekler} ({m.kalori} kcal, P:{m.protein}g K:{m.karb}g Y:{m.yag}g)\n"
        total_cal += m.kalori or 0

    # Değerlendirme metni kullanıcıya gösterilir → dile göre üret. meals_text içindeki
    # öğün/yemek adları (kanonik) girdi bağlamıdır; AI hedef dilde özetler.
    if current_locale() == "en":
        prompt = (
            "You are a nutrition coach. Write in English. Address the user as 'you'.\n\n"
            f"User's goal: {goal}\n"
            f"Daily calorie target: {round(target)} kcal\n"
            f"Total today: {round(total_cal)} kcal\n\n"
            f"What they ate today:\n{meals_text}\n"
            "Evaluate today:\n"
            "- Did they reach the calorie target?\n"
            "- Is the macro distribution balanced?\n"
            "- How is it in terms of bioavailability?\n"
            "- Is the gluten content high?\n"
            "- Is there anything that should change?\n"
            "Be short and specific, 4-5 sentences is enough."
        )
        system_prompt = "You are a nutrition coach. Speak briefly, specifically, in English."
    else:
        prompt = (
            f"Sen bir beslenme koçusun. Türkçe yaz, İngilizce kullanma.\n"
            f"Kullanıcıya 'sen' diye hitap et.\n\n"
            f"Kullanıcının hedefi: {goal}\n"
            f"Günlük kalori hedefi: {round(target)} kcal\n"
            f"Bugün toplam: {round(total_cal)} kcal\n\n"
            f"Bugün yedikleri:\n{meals_text}\n"
            f"Bu günü değerlendir:\n"
            f"- Kalori hedefine ulaştı mı?\n"
            f"- Makro dağılımı dengeli mi?\n"
            f"- Biyoyararlanım açısından nasıl?\n"
            f"- Gluten içeriği yüksek mi?\n"
            f"- Değiştirilmesi gereken bir şey var mı?\n"
            f"Kısa ve spesifik ol, 4-5 cümle yeterli."
        )
        system_prompt = "Sen bir beslenme koçusun. Kısa, spesifik, Türkçe konuş."

    try:
        review = _openai_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
            max_tokens=400,
            temperature=0.7,
        )
    except Exception:
        current_app.logger.exception("Öğün değerlendirmesi üretilemedi")
        review = t("route.eval_failed")

    return jsonify({
        "review": review,
        "total_calories": round(total_cal),
        # Yapılandırılmış hedef yoksa `null` — uydurma bir sayı DEĞİL.
        "target": round(configured.calories) if configured else None,
    })
