# Challenges (Sprint 5 PR3)

Haftalık meydan okumalar: mevcut gamification olaylarından **otomatik izlenen**
global challenge'lar + kullanıcının **katıldığı** öne-çıkan (featured) challenge'lar.
Tamamlanınca XP + rozet + bildirim + feed kilometre-taşı üretir. Amaç: yeni bir
cron/instance tablosu/Redis kurmadan, mevcut olay akışının üzerine ince bir
katman eklemek.

## Omurga (spine)

Tek huni: `app/services/challenges.py::record_event(user_id, event_type, amount=1)`.
Her gamification olayı buradan geçer; `event_type` (metric) ile eşleşen aktif
challenge'ları ilerletir. **COMMIT ETMEZ** — çağıranın transaction'ında çalışır
(`_claim_quest` sözleşmesi; ana eylemle atomik). Kendi hatasını yutar → antrenman/
öğün akışını asla kırmaz.

**Çağıran-güvenliği (no_autoflush + poison-protection).** Katalog/periyot okuması
`db.session.no_autoflush` altındadır (`notify`/`award_badge` deseni): record_event
çağıranın henüz commit'lenmemiş bekleyen yazılarını kendi SELECT'iyle erken FLUSH
ETMEZ — eşleşme yoksa çağıranın işine hiç dokunmaz. Eşleşme varsa, her challenge'ın
`begin_nested()` savepoint'i AÇILMADAN ÖNCE tek bir açık `db.session.flush()` ile
çağıranın yazıları dış transaction'a itilir; böylece bir challenge'ın savepoint
rollback'i (deadlock/kilit zaman aşımı) çağıranın işini GERİ ALMAZ (triage
2026-07-17 #2). Bu flush çağıranın KENDİ yazısıdır; ele alınmamış bir UNIQUE ihlali
içerse bile yutulur — record_event ana eylemi kırmaz.

İki tablo veri, bir tablo katalog:

- **`Challenge`** — statik seed'li katalog (DailyQuest deseni). `code`/`category`/
  `metric`/`badge_code`/`challenge_type`/`period_type` kanonik İngilizce slug;
  `title`/`description` kanonik TR (görünen metin `t_or("challenge.<code>.title", …)`
  ile çevrilir).
- **`UserChallengeProgress`** — kullanıcı×challenge×period ilerleme satırı.
  `uq_user_challenge_period` benzersizliği; `progress`, `opted_in`, `completed_at`.
- **`UserBadge`** — kazanılan rozetler (`uq_user_badge`), katalog `badges.py`'de.

## Olay taksonomisi + kanca haritası

`event_type` (= `Challenge.metric`) → onu ateşleyen çağrı yeri:

| event_type          | Nereden                                                        |
|---------------------|---------------------------------------------------------------|
| `login`             | `_claim_quest` (hooks login quest'i)                          |
| `workout_logged`    | `_claim_quest` (UI antrenman + AI koç pump check)            |
| `meal_logged`       | `_claim_quest` (öğün kaydı)                                   |
| `suggestion_sent`   | `_claim_quest` (arkadaşa mesaj)                              |
| `supplement_added`  | `_claim_quest`                                               |
| `water_logged`      | `_claim_quest`                                               |
| `checkin_done`      | `_claim_quest`                                               |
| `friend_invited`    | `_claim_quest`                                               |
| `pump_check_created`| `training.py` (UI pump check) + `ai_coach.py` (AI tool)      |
| `active_day`        | `hooks.update_streak` kilitli dal (Istanbul-günü başına 1 kez)|
| `xp_earned`         | `gamification.award_xp` (ödül XP'si HARİÇ — aşağı bak)       |

Quest olayları `_claim_quest` içinde TEK yerden `record_event(user_id, quest_type)`'e
delege edilir (yeni quest → otomatik challenge kancası; DailyQuest satırı olmasa
bile challenge ilerler). Doğrudan olaylar (`pump_check_created`/`active_day`/
`xp_earned`) ilgili yazma yerinde tek satırla çağrılır.

## Periyot matematiği (cron yok)

`period_key = "YYYY-Www"` **hesaplanır** — instance tablosu yok. Sınır
`_last_completed_week_key` ile birebir aynı: **Pazar 23:59 Istanbul**.

- `current_challenge_week(now=None)` — aktif haftanın ISO anahtarı; Pazar 23:59'dan
  ÖNCE bu ISO hafta, tam/sonra bir sonraki. `_last_completed_week_key`'in tersidir.
- `period_end_utc(now=None)` — yaklaşan Pazar 23:59 Istanbul → **naive UTC** (istemci
  geri sayımı ISO UTC + "Z" olarak alır; leaderboard `resetAt` ile aynı kaynak).

Yeni hafta = yeni `period_key` = yeni lazily-oluşturulan ilerleme satırları. Eski
satırlar tarihçe olarak kalır; sıfırlama/silme yok.

## Hibrit anlam (global auto vs featured opt-in)

- **global** — `record_event` satırı yoksa **get-or-create** eder (otomatik katılım).
  Get-or-create yarış-güvenli: `db.session.begin_nested()` (SAVEPOINT) + `IntegrityError`
  guard → eşzamanlı istek `uq_user_challenge_period`'ı ihlal ederse satırı yeniden
  okur, çağıranın transaction'ını ZEHİRLEMEZ.
- **featured** — yalnızca kullanıcının **mevcut opted_in** satırı ilerletilir;
  `record_event` featured için satır YARATMAZ. Katılım `join_featured(user_id,
  challenge_id)` ile (blueprint `POST /challenges/<id>/join`; global → 400).

## Tam-bir-kez tamamlama

`progress >= target_value` olunca `_try_complete` **korumalı UPDATE** çalıştırır:
`UPDATE … SET completed_at=now WHERE id=? AND completed_at IS NULL`. rowcount==1
kazanan tek istek ödülü verir → XP + rozet + bildirim + `challenge_completed` feed
aktivitesi TAM BİR KEZ. İkinci olay `completed_at` dolu olduğu için atlanır.

**Atomiklik (kısmi tamamlama yok).** Kazanma + tüm ödül yan etkileri `_try_complete`
içinde TEK `begin_nested()` savepoint'indedir: bir ödül adımı patlarsa (XP/rozet/
bildirim/feed) `completed_at` dahil hepsi geri alınır → "XP verilip rozet
verilmemiş" gibi kısmi başarı olamaz. Challenge tamamlanmamış kalır ve sonraki
olayda yeniden denenir. Bu atomiklik çağırandan BAĞIMSIZDIR — record_event'in
sarmalayan savepoint'i olmasa (gelecekteki doğrudan çağıranlar) da korunur.
Tam-bir-kez semantiği bozulmaz: kapı hâlâ guarded UPDATE'tir.

## XP özyineleme kuralı

Challenge ödülü XP'si `xp_earned` challenge'ını **beslememeli** (sonsuz döngü).
`award_xp(user_id, amount, count_challenge_xp=False)` challenge ödüllerinde ve
haftalık rollover top-3'te kullanılır; yalnızca kullanıcının "gerçek" XP kazanımı
`xp_earned` metriğini ilerletir.

## Genişletme tohumları

- `challenge_type` — şimdilik `global`|`featured`; ileride `duel`/`team`/`sponsored`.
- `period_type` — şimdilik `weekly`; ileride `daily`/`seasonal` (period math dallanır).
- `metric` serbest string → yeni olay kaynağı eklemek yalnızca yeni `record_event`
  çağrısı + seed satırı ister. **Deferred:** `running`/`cardio` kategorisi — istek-içi
  deterministik olay kaynağı yok; `metric` kolonu ileride destekler.

## Seed kataloğu

`seed_challenges()` (boot'ta `db_init` + `flask seed-quests` yanında; `code`'a göre
idempotent):

| code                    | type     | metric               | target | XP  | badge         |
|-------------------------|----------|----------------------|--------|-----|---------------|
| `weekly_workouts`       | global   | `workout_logged`     | 3      | 150 | —             |
| `weekly_meals`          | global   | `meal_logged`        | 10     | 100 | —             |
| `weekly_water`          | global   | `water_logged`       | 5      | 75  | —             |
| `weekly_pump`           | global   | `pump_check_created` | 3      | 100 | `pump_week`   |
| `weekly_active`         | global   | `active_day`         | 5      | 100 | `active_week` |
| `weekly_xp`             | global   | `xp_earned`          | 500    | 150 | —             |
| `featured_pump_perfect` | featured | `pump_check_created` | 5      | 300 | `pump_perfect`|
| `featured_grind`        | featured | `workout_logged`     | 5      | 250 | `grinder`     |

## Yeni challenge / metric ekleme

1. (Yeni metric ise) olay yerinde `record_event(user_id, "<metric>", amount)` çağır.
2. `CHALLENGE_SEED`'e bir dict ekle (kanonik TR title/description + slug alanlar).
3. `locales/{tr,en}.json`'a `challenge.<code>.title`/`.desc` ekle (parite şart).
4. (Rozet veriyorsa) `badges.py::BADGE_CATALOG` + `badge.<code>.title` i18n anahtarı.
5. Deploy additive: eski kod yeni satır/tabloyu yok sayar; migration
   `ff66aa77bb88` `has_table` kapılı ve re-runnable.

## Uçlar (blueprint)

- `GET /challenges` — sayfa kabuğu (`challenges.html`).
- `GET /challenges/data` — `{weekKey, periodEndsAt, challenges:[…], badges:[…]}`.
- `POST /challenges/<id>/join` — featured'a katıl (global → 400, idempotent).
- `GET /challenges/<id>/leaderboard?scope=friends|global` — `challenge_board` çıktısı.

Leaderboard sıralaması (`challenge_board`): `progress` desc → tamamlananlar
`completed_at` asc (nulls-last) → `user_id` asc; top 50 + kapsam dışıysa "me" satırı.
`friends` kapsamı `friends.get_friend_ids`'e delege eder. Redis yok — Postgres/SQLite
`ORDER BY`.
