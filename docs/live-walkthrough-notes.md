# FitX — Live Walkthrough Evidence Bundle
Date: 2026-06-14 · Reviewer drove the live site `https://fitx-chatbot.duckdns.org`
logged in as real user **yusuf** (Lvl 5, 2201 XP, 76.1 kg). Desktop viewport
(~1440px). This file is the shared evidence for the 3 persona reviews.

> Tooling note: Several pages (dashboard, /nutrition, /progress-page) never reach
> browser "document_idle" — there is persistent background JS (timer / animation /
> polling, likely `coach_widget.js` and/or chart/ring animations). Effect: after the
> first paint, screenshots/clicks/typing hang ~45s. This blocked reliable in-page
> form interaction (meal log, AI plan, menu scan) and is itself a **performance
> finding**. True mobile emulation was not possible (Chrome clamps min window width),
> so mobile is assessed from the mobile-first CSS + observed adaptive behavior.

---

## GLOBAL / CROSS-CUTTING OBSERVATIONS

### Navigation — TWO different shells in one app (major)
- **Mobile shell** (top bar: ☰ + "FITX" + avatar; bottom 4-tab action bar
  Ana Sayfa / Antrenman / Beslenme / Kulüp + center "+" FAB): used on
  **dashboard, /nutrition, /training, /progress-page, /quests, /leaderboard,
  /friends, /supplements**.
- **Desktop sidebar shell** (left rail, logo "**FC**", vertical nav: Ana Sayfa,
  Beslenme, Antrenman, Supplements, İlerleme, Arkadaşlar, Kulüp, Görevler + user
  card bottom): used on **/chat/<user>** and **/edit-profile** only.
- Result: clicking "Profil" or opening a chat throws the user into a completely
  different navigation paradigm. Jarring and inconsistent.

### Branding inconsistency
- Logo is "**FITX**" in the mobile header but "**FC**" in the desktop sidebar.
- Auth pages (from code/WebFetch) say "**FITNESS COACH**". Three names for one app.

### Language is inconsistently mixed (Turkish UI with English islands)
- Page TITLES often English: "**SUPPLEMENT STACK**".
- Quest names English: "Daily Login", "Log a Workout", "Help a Friend",
  "Update Your Stack" — while their descriptions are Turkish.
- Nav item "**Supplements**" English among Turkish siblings (Beslenme, Antrenman…).
- Supplement category/status labels English: Pre-Workout, Vitamin/Health, Active,
  Low Stock, Finished.

### Turkish-locale uppercase bug
- CSS `text-transform: uppercase` on English words in a Turkish context renders
  "Active" → "**ACTİVE**" (dotted capital İ). Visible on supplements & edit-profile.

### Heading style quirk
- Page headings split one word across two lines, 2nd half in volt-green. Works for
  two-word titles ("İLERLEME / TAKİBİ") but on the single word "ARKADAŞLARIN" it
  splits mid-stem → "**ARKADAŞ / LARIN**", reading like a broken hyphenation.

### Color/accent inconsistency
- Brand accent is volt-green `#CCFF00`, but: the "Sohbet" (chat) button is **blue**;
  edit-profile stat cards use **orange** (streak) and **blue** (XP) top borders;
  the weight sparkline is **red** (ambiguous semantics — red usually = bad, but here
  it's just the weight line). Macro colors are hardcoded inline, not tokens.

### Performance / front-end health
- **Avatar embedded as an inline base64 data-URI (~245 KB) in the HTML on every
  page** → no browser caching, bloats every response.
- **Duplicate API call**: `/nutrition-plan/active` is fetched **twice** on
  /nutrition load.
- **External Google Fonts** (fonts.googleapis.com + fonts.gstatic.com) are
  render-blocking third-party requests (Bebas Neue + DM Sans, multiple woff2).
- **Pages never go idle** (perpetual JS) — battery/perf cost on mobile.
- Static JS is cache-busted per-deploy (`?v=<timestamp>`) — fine.

### Data consistency
- Dashboard weight = **76.1 kg**; /progress-page check-in weight placeholder =
  **78.5 kg** — two different "current weight" values surfaced in different places.

### Discoverability / marketing surface (from code + WebFetch of /login)
- **No public landing page** — every route except `/health` is behind login. A
  first-time visitor sees only a bare login form ("FITNESS COACH" + 2 fields), with
  zero value proposition, screenshots, features, or social proof.
- **No favicon, no meta description, no Open Graph / Twitter cards** → poor sharing
  and SEO. Google Analytics (`G-YXSGLN7C7Y`) loads only on the dashboard.

### Route-naming smell (bug-level)
- **`/progress` returns raw JSON `[]`** (it's the weight-log API). The actual
  progress UI is at **`/progress-page`**. A user who hits `/progress` (link,
  bookmark, guess) gets a raw JSON dump in the browser's JSON viewer.

---

## PER-SCREEN NOTES

### 1. Dashboard `/` ("İYİ AKŞAMLAR, YUSUF" / "AKŞAM — COMMAND CENTER")
- Bento grid: **Günlük Kalori** (calorie ring 0 kcal / 0%, Tüketilen/Kalan/Aktivite/
  Hedef), **Aktivite Takibi** (step tracker: Hafif/Orta/Tempolu/Hızlı + steps input
  + KAYDET), **Kilo Takibi** (76.1 kg, red sparkline, GÜNCELLE, BMR 1789 / TDEE 3130
  / HEDEF 2730), **Günün İpucu** (tip carousel w/ Önceki/Sonraki), **Fitness Yolcusu**
  XP card (Lvl 5, 201/500 XP, 🔥1 gün, "3 gün seri yap → ×1.1 XP Bonus").
- Layout: noticeable **whitespace imbalance** on desktop (short Aktivite card next to
  tall Kilo card; large empty area below the XP card). Reads as a stretched mobile
  layout centered on a wide screen.
- **Tip carousel glitch**: during the slide transition the tip text briefly
  overlaps/clips against the emoji/card edge (caught mid-animation).
- Empty/zero state: calorie ring at 0 with no prompt to log the first meal.

### 2. Nutrition `/nutrition` ("BESLENME")
- Tabs: **Bugün / Günlük / AI Plan / Geçmiş / Su Takibi**.
- Today: calorie ring (0 kcal, 0%), macro bars (Protein/Karbonhidrat/Yağ 0g),
  good empty state ("Bugün henüz öğün girilmedi…").
- **Three overlapping ways to log food**: (a) "HIZLI EKLE" quick-add cards from the
  active plan (Kahvaltı/Öğle/Akşam/Ara Öğün Plan A + Su), (b) "MANUEL EKLE" meal-type
  buttons + ÖĞÜN KAYDET, (c) the separate "Günlük" (diary) tab. Unclear which path a
  user should use; high cognitive load.
- This page exhibits the never-idle behavior; couldn't reliably interact.

### 3. Training `/training` ("ANTRENMAN PROGRAMI")
- "AKTİF PROGRAMIN" created 13.05.2026, labeled "Mükemmel Program", score **8.3/10**.
- "BUGÜN: GÖĞÜS VE SIRT" banner.
- Weekly grid Pazartesi–Pazar with focus/Süre/Kalori/exercises; today highlighted.
- **Content quality issue**: the AI plan has **7 training days, zero rest days**
  (Pzt strength, Salı "Bisiklet", Çar strength, Per "Bisiklet", Cuma strength,
  Cmt "Bisiklet", Paz strength). No recovery day is poor programming and could be
  surfaced as bad advice. Cardio days are just "Bisiklet" with no detail.
- Stats row: 7 ANTRENMAN GÜNÜ / 2010 HAFTALIK KALORİ / 270 TOPLAM DAKİKA.
- Actions: "✓ Antrenman Tamamlandı", "↺ Programı Sıfırla / Yeni Plan Oluştur".

### 4. Progress `/progress-page` ("İLERLEME TAKİBİ")
- Tabs: **Check-in / Grafikler / Geçmiş**.
- Weekly check-in form: Güncel Kilo (placeholder 78.5), Not, and 4 sliders all
  defaulting to 3 — Antrenman Yoğunluğu, Yorgunluk/Fatigue, Uyku Kalitesi, Beslenme
  Uyumu (each with Çok düşük↔Çok yüksek style labels) + "Progressive Overload" Q.
- Grafikler tab is Chart.js (canvas) — contributes to the never-idle behavior.
- (See route-naming smell above re: `/progress` JSON.)

### 5. Quests `/quests` ("GÖREVLER")
- Rank banner: 🌱 Fitness Yolcusu, 2201 XP · Seviye 5.
- 4 daily quests: Daily Login (+10, ✓ Tamamlandı), Log a Workout (+50, ✓ Tamamlandı),
  Help a Friend (+30, Bekliyor), Update Your Stack (+25, Bekliyor).
- Issues: English quest names in Turkish UI; **two different quests share the same 🤝
  emoji** (Help a Friend AND Update Your Stack); only 4 quests (shallow); 3-up grid
  leaves one orphan card + large empty page. Rewards appear auto-granted (no claim
  interaction / dopamine moment).

### 6. Leaderboard `/leaderboard` ("KULÜP LİDERLİK")
- Filters: "Tüm Zamanlar" dropdown (Tüm Zamanlar/Haftalık) + tabs Arkadaşlar/Genel.
- **Loading flash** ("YÜKLENİYOR…") before data appears (client-fetched after paint).
- Only **2 users total**: 1. yusuf LV5 2.201 XP 🔥1; 2. "**test**" LV1 60 XP 🔥1.
  A near-empty board, with a competitor literally named "test", reads as dead/unfinished.

### 7. Friends `/friends` ("ARKADAŞLARIN")
- Username search + "ARKADAŞLARIM (1)": one friend "test" (@test, Fitness Yolcusu)
  with a blue **💬 Sohbet** button. No pending-requests content (none pending).
- Title word-split issue (ARKADAŞ / LARIN). Sparse page.

### 8. Chat `/chat/test` ("SOHBET — test") — DESKTOP SIDEBAR SHELL
- Header: partner test (@test, Fitness Yolcusu, Lvl 1) + 💡 icon.
- Message types: plain text, **🍎 ÖĞÜN ÖNERİSİ** (meal suggestion) and
  **💪 ANTRENMAN ÖNERİSİ** (workout suggestion) cards with "✅ Kabul edildi", plus
  auto "✅ Önerini kabul ettim: …" echo messages. Sent bubbles = volt-green (right),
  received = dark (left). Input "Mesajını yaz…" + send.
- Issues: **timestamp ordering looks wrong** — sequence 14:31 → 14:54 → 15:18 → 15:21
  then a final card at **12:15** at the bottom (newest position). Each accepted
  suggestion creates a duplicate "Önerini kabul ettim" echo, doubling the message
  volume. Stray text in a meal name ("pirinç patlağı i 1 ölçek…").

### 9. Supplements `/supplements` ("SUPPLEMENT STACK")
- Add form: Ürün Adı*, Marka*, Kategori chips (Protein/Amino Acid/Pre-Workout/
  Vitamin-Health/Creatine/Other), Durum (Active/Low Stock/Finished), 4 star ratings
  (Etki/Lezzet/Sindirim/Fiyat-Perf.), Ödenen Fiyat (TL), Profilde Göster toggle,
  Yorum, EKLE. Long form for an optional/side feature.
- "MEVCUT STACK'İN": Subzero (Hiq·Pre-Workout, "Fena değil"), Hi Pro (Hiq·Protein,
  "Tadı güzel.") with per-item ratings + Sil.
- English-heavy labels + ACTİVE uppercase bug. No tie-in to nutrition/meal planning.

### 10. Edit Profile `/edit-profile` ("PROFİL AYARLARI") — DESKTOP SIDEBAR SHELL
- Avatar (real photo), name "YUSUF ARSLAN" @yusuf; two stat cards 🔥1 GÜNLÜK SERİ
  (orange border), ⭐2201 XP·LVL5 (blue border).
- Form: Ad Soyad, Kullanıcı Adı (min 3 chars), Fitness Hedefi (Kilo Verme / Kas
  Kazanma), Hedef Kilo, KAYDET. Then "GÜNCEL SUPPLEMENT STACK" duplicates the
  supplements list again + "Stack'ini Düzenle →".

### 11. Onboarding `/setup` ("HOŞ GELDİN yusuf")
- Clean **multi-step wizard** (step 1 of ~5; progress dots). Step 1 "FİZİKSEL
  BİLGİLERİN": Kilo, Boy, Yaş, Cinsiyet (Erkek/Kadın) + "DEVAM ET". Theme toggle
  top-right. Full-screen, no nav shell — appropriate.
- The single best-designed flow in the app. BUT it opens straight into body-metric
  collection with only one line of context ("Seni tanıyalım — koçun sana özel bir
  plan oluştursun") — no "what is FitX / what will I get" value framing before asking
  for personal data.
- Note: visiting /setup when already onboarded re-opens the wizard with empty fields
  (could overwrite an existing profile/plan if submitted).

### Public auth pages (from WebFetch /login + code; not re-driven to avoid logout)
- Login: "FITNESS COACH", "Hesabına giriş yap", Kullanıcı Adı + Şifre, "GİRİŞ YAP",
  "Hesabın yok mu? Kayıt ol". Bare, no marketing. Register adds email + password
  rules hint. Theme toggle present.

---

## SCREEN INVENTORY (captured this session)
Dashboard, /nutrition (Bugün), /training, /progress (JSON bug), /progress-page,
/quests, /leaderboard, /friends, /chat/test, /supplements, /edit-profile, /setup.
Drawer + FAB exist (mobile shell) but their open state could not be screenshotted
due to the idle-gating; contents known from the sidebar nav + code.
