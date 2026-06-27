"""i18n (TR/EN) altyapısı testleri: t() fallback, locale çözümü, /set-language,
kayıtta dil kalıcılığı ve koç sistem promptunun dile göre değişmesi."""


def test_t_explicit_locale_and_fallback():
    from app.i18n import t
    # EN sözlükten birebir
    assert t("login.submit", locale="en") == "LOG IN"
    # TR sözlükten birebir
    assert t("login.submit", locale="tr") == "GİRİŞ YAP"
    # Eksik anahtar → anahtarın kendisi (kırılmaz)
    assert t("does.not.exist", locale="en") == "does.not.exist"
    # Interpolasyon
    assert t("auth.welcome", locale="en", username="Sam") == "Welcome Sam!"
    assert t("auth.welcome", locale="tr", username="Sam") == "Hoş geldin Sam!"


def test_en_falls_back_to_tr_for_missing_key(monkeypatch):
    import app.i18n as i18n
    # en'de olmayan ama tr'de olan kurgusal bir anahtar → tr değerine düşer
    monkeypatch.setitem(i18n._CATALOG["tr"], "only.in.tr", "yalnız tr")
    assert "only.in.tr" not in i18n._CATALOG.get("en", {})
    assert i18n.t("only.in.tr", locale="en") == "yalnız tr"


def test_build_coach_system_language():
    from app.services.ai_coach import build_coach_system
    en = build_coach_system("en")
    tr = build_coach_system("tr")
    assert "ENGLISH" in en or "English" in en
    assert "Türkçe" in tr
    # Geçersiz dil → tr'ye düşer
    assert build_coach_system("zz") == tr


def test_set_language_endpoint_switches_session(client):
    r = client.post("/set-language", json={"lang": "en"})
    assert r.status_code == 200
    assert r.get_json()["language"] == "en"
    # Sonraki istek İngilizce render etmeli (anon → session['lang'])
    page = client.get("/login")
    body = page.get_data(as_text=True)
    assert "Sign in to your account" in body
    assert "Hesabına giriş yap" not in body


def test_set_language_rejects_invalid(client):
    r = client.post("/set-language", json={"lang": "de"})
    assert r.status_code == 400


def test_default_locale_is_turkish(client):
    body = client.get("/login").get_data(as_text=True)
    assert "Hesabına giriş yap" in body


def test_register_persists_language(app, client):
    from app.models import User
    r = client.post("/register", json={
        "username": "enuser", "email": "enuser@example.com",
        "password": "Sifre123", "language": "en",
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    user = User.query.filter_by(username="enuser").first()
    assert user is not None
    assert user.language == "en"


def test_register_defaults_language_to_tr(app, client):
    from app.models import User
    client.post("/register", json={
        "username": "truser", "email": "truser@example.com",
        "password": "Sifre123",
    })
    user = User.query.filter_by(username="truser").first()
    assert user is not None
    assert user.language == "tr"


def test_landing_renders_localized(client):
    # Landing /welcome'da (anonim); / login-gated dashboard.
    tr_body = client.get("/welcome").get_data(as_text=True)
    assert "Ücretsiz Başla" in tr_body
    # EN'e geç → İngilizce gövde
    client.post("/set-language", json={"lang": "en"})
    en_body = client.get("/welcome").get_data(as_text=True)
    assert "Start Free" in en_body
    assert "Ücretsiz Başla" not in en_body


def test_dashboard_renders_localized(app, client, make_user, login):
    # Dashboard (/) login + profile_complete ister.
    make_user("dashen", profile_complete=True, language="en")
    login("dashen")
    body = client.get("/").get_data(as_text=True)
    assert "Daily Calories" in body and "Activity Tracking" in body
    assert "Günlük Kalori" not in body
    # EN tip dizisi seçili olmalı (TR tip metni gövdede olmamalı)
    assert "Sports Physiology" in body


def test_nutrition_renders_localized(app, client, make_user, login):
    make_user("nuten", profile_complete=True, language="en")
    login("nuten")
    r = client.get("/nutrition")
    assert r.status_code == 200, r.status_code
    body = r.get_data(as_text=True)
    assert "Today's Meals" in body and "Manual Add" in body and "LOG MEAL" in body
    assert "Bugünkü Öğünler" not in body
    # Öğün tipi data-args TR kalmalı (backend kanonik değer)
    assert 'data-args=\'["Kahvaltı"]\'' in body


def test_training_renders_localized(app, client, make_user, login):
    make_user("tren", profile_complete=True, language="en")
    login("tren")
    r = client.get("/training")
    assert r.status_code == 200, r.status_code
    body = r.get_data(as_text=True)
    # UI chrome İngilizce
    assert "Training Style" in body and "Equipment" in body and "CREATE MY PROGRAM" in body
    assert "Antrenman Tarzı" not in body
    # Kuplaj koruması: OPTIONS val kodları + gün adları + pump değerleri TR kalır
    assert '"spor_salonu"' in body and '"tum_vucut"' in body      # OPTIONS val
    assert "getTodayTurkish" in body and "'Pazartesi'" in body     # backend gün eşleşmesi
    assert '<option value="Spor Salonu"' in body                   # pump-location değeri
    assert "fitx_workout_completed_" in body                       # localStorage anahtarı


def test_secondary_pages_render_en(app, client, make_user, login):
    make_user("secen", profile_complete=True, language="en")
    login("secen")
    checks = {
        "/quests": ("Complete your daily quests", "Günlük görevlerini"),
        "/premium": ("GO PREMIUM", "PREMIUM'A GEÇ"),
        "/friends": ("INVITE A FRIEND", "ARKADAŞINI DAVET ET"),
        "/leaderboard": ("All Time", "Tüm Zamanlar"),
    }
    for path, (en_str, tr_str) in checks.items():
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code)
        body = r.get_data(as_text=True)
        assert en_str in body, path
        assert tr_str not in body, path
    # leaderboard kuplaj: timeframe/scope değerleri kanonik kalır
    lb = client.get("/leaderboard").get_data(as_text=True)
    assert 'value="all_time"' in lb and 'data-args=\'["friends"]\'' in lb


def test_final_pages_render_en(app, client, make_user, login):
    make_user("finen", profile_complete=True, language="en")
    login("finen")
    for path, en_str, tr_str in [
        ("/progress-page", "Weekly Check-In", "Haftalık Check-In"),
        ("/supplements", "ADD NEW SUPPLEMENT", "YENİ SUPPLEMENT EKLE"),
    ]:
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code)
        body = r.get_data(as_text=True)
        assert en_str in body and tr_str not in body, path
    # setup: profili TAMAMLANMAMIŞ kullanıcı görür
    make_user("setupen", language="en")
    login("setupen")
    sbody = client.get("/setup").get_data(as_text=True)
    assert "What's Your Goal?" in sbody and "Hedefin Ne?" not in sbody
    # setup kuplaj: seçenek değerleri kanonik (backend) kalır
    assert '["goal","kilo verme"]' in sbody and '["level","beginner"]' in sbody


def test_nutrition_js_keeps_canonical_values():
    """nutrition.js kuplaj koruması: görünen metin EN olabilir ama backend'e giden
    KANONIK değerler (öğün tipi, plan besin adları, diary öğün anahtarları) Türkçe
    KALMALI — aksi halde FatSecret araması / MealLog.ogun eşleşmesi bozulur."""
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "nutrition.js")
    js = open(p, encoding="utf-8").read()
    assert "selectedMealType = 'Kahvaltı'" in js
    assert "'Tavuk Göğsü'" in js and "'Pirinç'" in js   # plan besin değerleri (AI prompt'a gider)
    assert "{ key: 'Kahvaltı'" in js                     # diary öğün anahtarı (backend meal_name)
    # i18n alias + görünen-etiket map'leri kurulu
    assert "var __t" in js and "MEAL_LABELS_EN" in js and "FOOD_LABELS_EN" in js


def test_authenticated_locale_follows_user(app, client, make_user, login):
    """Girişli kullanıcının language alanı locale'i belirler (session'dan bağımsız)."""
    make_user("enfan", language="en")
    login("enfan")
    body = client.get("/login").get_data(as_text=True)
    # /login GET girişliyken de render edilir; EN locale beklenir
    assert "Sign in to your account" in body


# ── Backend-kaynaklı metinler (görev/seviye başlıkları, premium özellikleri) ──

def test_level_title_localized():
    """level_title() seviye ünvanını dile göre verir; get_title() KANONIK (tr) kalır."""
    from app.services.gamification import get_title, level_title
    assert level_title(1, locale="en") == "Fitness Traveler"
    assert level_title(1, locale="tr") == "Fitness Yolcusu"
    assert level_title(25, locale="en") == "AxisAI Legend"
    # Kanonik değer dilden bağımsız (feed içeriği + dahili kullanım buradan)
    assert get_title(1) == "Fitness Yolcusu"
    assert get_title(25) == "AxisAI Efsanesi"


def test_quest_titles_localized(app, client, make_user, login):
    """Görev başlık/açıklaması GÖRÜNEN katmanda dile göre; DB seed'i TR kanonik kalır."""
    from app.extensions import db
    from app.models import DailyQuest
    db.session.add(DailyQuest(title="Günlük Giriş", description="Bugün uygulamaya giriş yap",
                              points_reward=10, quest_type="login"))
    db.session.add(DailyQuest(title="Öğün Kaydet", description="Bugün bir öğün kaydet",
                              points_reward=20, quest_type="meal_logged"))
    db.session.commit()
    make_user("questen", language="en")
    login("questen")
    body = client.get("/quests").get_data(as_text=True)
    assert "Daily Login" in body and "Log a Meal" in body
    assert "Günlük Giriş" not in body and "Öğün Kaydet" not in body


def test_quest_title_falls_back_to_db_for_unknown_type(app, client, make_user, login):
    """Katalogda anahtarı olmayan quest_type → ham anahtar DEĞİL, kanonik DB metni."""
    from app.extensions import db
    from app.models import DailyQuest
    db.session.add(DailyQuest(title="Özel Görev", description="Özel açıklama",
                              points_reward=15, quest_type="custom_xyz"))
    db.session.commit()
    make_user("fallbacken", language="en")
    login("fallbacken")
    body = client.get("/quests").get_data(as_text=True)
    assert "Özel Görev" in body            # DB metni gösterildi
    assert "quest.custom_xyz.title" not in body  # ham anahtar SIZMADI


def test_drawer_level_title_localized(app, client, make_user, login):
    """inject_rank → drawer/rank ünvanı kullanıcının diline göre (level_title)."""
    make_user("ranken", language="en")     # rank_points=0 → seviye 1
    login("ranken")
    body = client.get("/quests").get_data(as_text=True)
    assert "Fitness Traveler" in body and "Fitness Yolcusu" not in body


def test_premium_features_localized(app, client, make_user, login):
    """Premium/free özellik listeleri dile göre üretilir (FREEMIUM katalog anahtarları)."""
    make_user("premen", language="en")
    login("premen")
    body = client.get("/premium").get_data(as_text=True)
    assert "1 AI plan per week" in body and "Unlimited re-planning" in body
    assert "Haftada 1 yapay zekâ planı" not in body
    # TR kullanıcı Türkçe görür
    make_user("premtr", language="tr")
    login("premtr")
    tr_body = client.get("/premium").get_data(as_text=True)
    assert "Haftada 1 yapay zekâ planı" in tr_body


# ── AI plan üretimi: prompt dile göre, JSON anahtarları/kanonik alanlar TR kalır ──

def test_nutrition_plan_prompt_localized(app, client, make_user, login, monkeypatch):
    """EN kullanıcıda beslenme planı prompt'u İngilizce içerik ister; JSON ANAHTARLARI
    (planlar/kahvalti/...) her dilde TR kalır (frontend kontratı)."""
    from app.extensions import db
    from app.models import UserSession
    import app.blueprints.nutrition.plan as planmod

    cap = {}
    def fake_heavy(messages, system_prompt=None, **kw):
        cap["prompt"] = messages[0]["content"]
        cap["system"] = system_prompt
        return '{"planlar": []}'
    monkeypatch.setattr(planmod, "_heavy_chat", fake_heavy)

    u = make_user("nutplanen", language="en")
    login("nutplanen")
    db.session.add(UserSession(user_id=u.id, target_calories=2000, goal="kilo verme"))
    db.session.commit()
    payload = {"proteins": ["Tavuk Göğsü"], "carbs": ["Pirinç"], "fats": ["Zeytinyağı"]}
    r = client.post("/nutrition-plan", json=payload)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert "ENGLISH" in cap["prompt"]                 # içerik dili EN
    assert '"planlar"' in cap["prompt"]               # JSON anahtarları TR (kontrat)
    assert "Türkçe" not in (cap["system"] or "")


def test_nutrition_plan_prompt_turkish_for_tr_user(app, client, make_user, login, monkeypatch):
    from app.extensions import db
    from app.models import UserSession
    import app.blueprints.nutrition.plan as planmod

    cap = {}
    monkeypatch.setattr(planmod, "_heavy_chat",
                        lambda messages, system_prompt=None, **kw: cap.update(
                            prompt=messages[0]["content"]) or '{"planlar": []}')
    u = make_user("nutplantr", language="tr")
    login("nutplantr")
    db.session.add(UserSession(user_id=u.id, target_calories=2000, goal="kilo verme"))
    db.session.commit()
    r = client.post("/nutrition-plan", json={"proteins": ["Tavuk Göğsü"],
                                             "carbs": ["Pirinç"], "fats": ["Zeytinyağı"]})
    assert r.status_code == 200
    assert "Türkçe yaz" in cap["prompt"] and "ENGLISH" not in cap["prompt"]


def test_training_plan_prompt_localized_keeps_canonical_fields(app, client, make_user, login, monkeypatch):
    """EN antrenman planı: içerik EN istenir AMA 'gun' (gün adı) ve 'tip' alanları
    KANONIK TÜRKÇE kalmalı (getTodayTurkish eşleşmesi + JS tip mantığı)."""
    from app.extensions import db
    from app.models import UserSession
    import app.blueprints.training as trainmod

    cap = {}
    def fake_heavy(messages, system_prompt=None, **kw):
        cap["prompt"] = messages[0]["content"]
        cap["system"] = system_prompt
        return '{"program": [], "haftalik_ozet": {}}'
    monkeypatch.setattr(trainmod, "_heavy_chat", fake_heavy)

    u = make_user("trplanen", language="en")
    login("trplanen")
    db.session.add(UserSession(user_id=u.id, goal="kas_kutlesi", fitness_level="beginner",
                               current_activity="orta", tdee=2400))
    db.session.commit()
    r = client.post("/training-plan", json={"gun_sayisi": 3})
    assert r.status_code == 200, r.get_data(as_text=True)
    p = cap["prompt"]
    assert "ENGLISH" in p                              # içerik dili EN
    assert "Pazartesi" in p and "antrenman" in p       # gun/tip kanonik TR korunur
    assert '"gun"' in p and '"tip"' in p
    # system prompt da kanonik alanları korumayı söylüyor
    assert "antrenman/dinlenme/kardiyo" in cap["system"]


def test_plan_no_session_error_localized(app, client, make_user, login):
    """Plan üretim hata mesajları (oturum yok) dile göre döner."""
    make_user("noplanen", language="en")
    login("noplanen")
    r = client.post("/nutrition-plan", json={"proteins": ["x"], "carbs": ["y"], "fats": ["z"]})
    assert r.status_code == 400
    assert r.get_json()["error"] == "First create your plan from the home page."


def test_training_html_day_label_coupling():
    """training.html: gün adı GÖRÜNEN map'i var; eşleşme hâlâ kanonik TR güne dayanır."""
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "training.html")
    html = open(p, encoding="utf-8").read()
    assert "DAY_LABELS_EN" in html and "function dayLabel" in html
    assert "esc(dayLabel(gun.gun))" in html               # görünen ad çevrilir
    assert "gun.gun === todayName" in html                # eşleşme kanonik TR kalır
    assert "'Pazartesi':'Monday'" in html


# ── Route mesajları (jsonify error/message) dile göre ──

def test_route_error_messages_localized(app, client, make_user, login):
    """Blueprint jsonify hata mesajları kullanıcının diline göre döner."""
    make_user("otherperson")                 # var olan ama arkadaş OLMAYAN kullanıcı
    make_user("routeen", language="en")
    login("routeen")
    # arkadaş olmayan kullanıcıyla sohbet → "You're not friends." (EN)
    r = client.get("/chat/otherperson/messages")
    assert r.status_code == 403, r.status_code
    assert r.get_json()["error"] == "You're not friends."


def test_route_message_turkish_for_tr_user(app, client, make_user, login):
    make_user("otherperson2")
    make_user("routetr", language="tr")
    login("routetr")
    r = client.get("/chat/otherperson2/messages")
    assert r.status_code == 403
    assert r.get_json()["error"] == "Arkadaş değilsiniz."


def test_edit_profile_renders_en(app, client, make_user, login):
    """edit_profile (ayarlar) sayfası EN render; dil endonimi 'Türkçe' korunur,
    hedef data-args kanonik (backend) kalır."""
    make_user("epen", language="en")
    login("epen")
    body = client.get("/edit-profile").get_data(as_text=True)
    assert "EDIT YOUR INFO" in body and "Target Weight" in body and "SAVE" in body
    assert "BİLGİLERİNİ DÜZENLE" not in body and "Hedef Kilo" not in body
    assert "Türkçe" in body                         # dil adı kendi dilinde
    assert '["kilo verme"]' in body                 # goal kanonik değer


def test_chat_page_renders_en(app, client, make_user, login):
    """chat sayfası EN render; öneri tipi kodları (message_type/data-type) kanonik kalır."""
    from app.extensions import db
    from app.models import Friendship
    u = make_user("chatme", language="en")
    friend = make_user("chatfriend")
    db.session.add(Friendship(sender_id=u.id, receiver_id=friend.id, status="accepted"))
    db.session.commit()
    login("chatme")
    body = client.get("/chat/chatfriend").get_data(as_text=True)
    assert "SUGGEST TO A FRIEND" in body and "Type your message" in body
    assert "ARKADAŞINA ÖNER" not in body and "Mesajını yaz" not in body
    assert "suggestion_meal" in body                # kanonik öneri tipi kodu


def test_workout_already_done_uses_structured_code(app, client, make_user, login):
    """Kuplaj düzeltmesi: 'zaten tamamlandı' artık metin değil yapısal `code` ile
    bildirilir (error metni i18n'lendi, frontend code'a bakar)."""
    from app.extensions import db
    from app.models import PumpCheck, TrainingPlan
    u = make_user("dblworkout", language="en")
    login("dblworkout")
    # complete_workout önce aktif TrainingPlan ister; sonra bugünkü PumpCheck'i
    # görünce "already_completed" döndürür.
    db.session.add(TrainingPlan(user_id=u.id, plan_data="{}"))
    db.session.add(PumpCheck(user_id=u.id, valid=True, location_type="Spor Salonu"))
    db.session.commit()
    r = client.post("/workout/complete", json={"image": "x"})
    assert r.status_code == 400
    body = r.get_json()
    assert body["code"] == "already_completed"
    assert body["error"] == "You've already completed today's workout!"  # EN
    # Frontend artık ham TR substring'ine ('zaten') değil code'a bakar
    import os
    th = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "training.html")
    assert "data.code === 'already_completed'" in open(th, encoding="utf-8").read()
