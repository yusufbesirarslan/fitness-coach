# Menü Makrolarında Porsiyon Eşleşme Hatası

> Durum: **düzeltildi** — porsiyon bandı (`nutrition_pipeline.check_portion_band`)
> + per-serving güven kapısı / 100g-eşdeğeri dönüşüm (`gate_per_serving`)
> + tür-bazlı gram yedeği (`DISH_SERVING_DEFAULT_G`).
> İlgili düzeltmeler: alaka-kapısı + TR→EN normalizasyon (`c1efbfd`), FatSecret
> aile-çökmesi (`57683a6`), saf-yağ bileşeni (`d53f093`), yemek-türü ana-ad +
> kategori kapısı (bu PR). Bu belge, o kapıların **kapsamadığı** ayrı bir hata
> sınıfını tanımlar.

## Özet

Alaka / spesifiklik / yemek-türü kapıları artık **doğru yemeğin** seçilmesini
sağlıyor (doğru aile, doğru tür). Ama doğru tanımlanmış bir yemek bile **yanlış
porsiyonla** gelebilir. Saha çıktısında (`ai-chatbot-menu.txt`) görülen düşük
değerler bu hatadır:

| Öğe | Çıktı | Gerçekçi restoran porsiyonu |
|---|---|---|
| Penne Arrabbiata | **180 kcal** · 5g P · 29g K · 4g Y | ~400–600 kcal |
| Vegan Burger | **170 kcal** · 5g P · 20g K · 8g Y | ~400–550 kcal |

Tersi de mümkün (aşırı-büyük eşleşme): Tavuk Mangal 956 kcal / 109g protein gibi
değerler de aynı sınıfın "üst" ucudur.

## Kök neden

Menü makro hattında (`_lookup_macros_fatsecret`,
`app/services/fatsecret.py`) bir FatSecret kaydının porsiyonu iki yoldan biriyle
işlenir:

1. **`is_per_serving` = True → değer OLDUĞU GİBİ kabul edilir.**
   `app/services/fatsecret.py:579`
   ```python
   if is_serv:
       per_serving[name] = macros   # ölçeklenmez, doğrudan tam porsiyon sayılır
   ```
   FatSecret çoğunlukla **ABD merkezli jenerik** bir veritabanıdır ve "serving"
   birimi küçük bir referans miktardır: **tek köfte (patty), ½ su bardağı, 1 dilim**.
   Yani "Veggie Burger – per 1 patty = 170 kcal" (ekmeksiz, sadece köfte) ya da
   "Penne Arrabbiata – per 1 serving ≈ 140 g = 180 kcal" kaydı, **tüm tabak**
   sanılarak yazılır. Sonuç: bütün bir restoran yemeği 2–3× eksik sayılır.

2. **`is_per_serving` = False → per-100g kabul edilip ağırlıkla ölçeklenir.**
   `app/blueprints/menu.py:276`
   ```python
   scale = grams / 100.0   # grams = _estimate_serving_weights_llm(...)
   ```
   Bu yol genelde sağlıklıdır (LLM, yemek-türüne göre 300–400 g tahmin eder), ama
   tahmin başarısız/aralık-dışı olduğunda **150 g sabit yedeğe** düşer
   (`app/services/ai_nutrition.py:457`). Türü gereği 300–400 g olması gereken bir
   makarna/burger bu durumda yarıya iner.

Ek olarak, en spesifik aday bir **alt-bileşen** olabilir (ekmeksiz köfte, sossuz
makarna); o zaman per-serving değeri yemeğin yalnızca bir parçasını yansıtır.

## Mevcut kapılar bunu neden yakalamıyor?

- `_is_specific_match` ve yemek-türü kapısı **KİMLİĞİ** doğrular, **MİKTARI** değil.
  170 kcal'lik bir burger "yanlış yemek" değildir — sadece tam porsiyon değildir,
  dolayısıyla kapılardan geçer.
- `nutrition_pipeline.check_serving` yalnızca **termodinamik olarak imkânsız**
  girdileri eler (100 g başına >900 kcal, P+C+F > ağırlık, mutlak tavanlar). 170
  kcal fazlasıyla "mümkün" bir değerdir → elenmez (`app/blueprints/menu.py` skor
  öncesi kontrol).

Yani sorun bir **alt/üst sınır (porsiyon makullüğü)** boşluğudur, kimlik değil.

## Tutarsızlık notu

Koç hattı (`_food_search_fatsecret`) per-serving kaydı `_estimate_serving_weights_llm`
ile **100 g'a normalize eder**; menü hattı (`_lookup_macros_fatsecret`) ise
per-serving'i **doğrudan kullanır**. İki hattın porsiyon ele alışı farklı — düzeltme
bunları yakınlaştırmalı.

## Önerilen düzeltme yönü (uygulandı)

1. **Yemek-türüne göre porsiyon bandı (alt/üst sınır).** Makro çözüldükten sonra
   toplam kaloriyi türe göre makul bir aralıkla karşılaştır (örn. burger ~350–800,
   makarna tabağı ~350–700, salata ~150–600, çorba ~150–400 kcal). Değer alt sınırın
   altındaysa: per-serving'i güvenme; per-100g/türetilmiş değeri yemek-türü servis
   ağırlığına ölçekle ya da LLM porsiyon tahminine düş.
   → `nutrition_pipeline.PORTION_KCAL_BANDS` + `check_portion_band`; per-serving
   kabul noktasında `gate_per_serving` (`_lookup_macros_fatsecret` içinde), band
   üstü kayıt atlanır, band altı 100g-eşdeğerine çevrilip per-100g yoluna verilir.
   Çözüm-sonrası ek log: `menu.py` `PORTION BAND LOW/HIGH`.
2. **Küçük FatSecret servinglerini per-100g muamelesi yap.** Per-serving kaydının
   metrik ağırlığı türün asgari gramından küçükse, onu 100 g eşdeğeri kabul edip
   yeniden ölçekle (mevcut `_estimate_serving_weights_llm` mantığını kullan).
   → `DISH_SERVING_MIN_G` + `gate_per_serving(serving_grams=...)`; ağırlık
   `estimate_serving_grams` ile deterministik tahmin edilir, oran dönüşümü
   900 kcal/100g tavanını aşarsa as-is 100g-eşdeğerine düşülür.
3. **150 g sabit yedeği sıkılaştır.** Bilinen büyük porsiyonlu türler için
   (makarna/burger/ana yemek) düz 150 g yedeğinden kaçın; tür-bazlı bir varsayılan
   kullan.
   → `DISH_SERVING_DEFAULT_G` + `_estimate_serving_weights_llm(fallback_weights=...)`;
   tür çözümü `_primary_dish_type(ad, kategori)` (koç hattı 150 g varsayılanını korur).

Bu yön, mevcut mimariyi korur: kapılar saf fonksiyon kalır, porsiyon bandı kontrolü
`nutrition_pipeline`'a deterministik bir kural olarak eklenebilir; LLM yalnızca
yedek olarak devreye girer.
