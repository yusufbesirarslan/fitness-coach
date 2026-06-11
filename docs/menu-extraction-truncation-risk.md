# Risk: Menü çıkarımında `max_tokens` ile öğe kapasitesi hizasız

**Durum:** Açık (henüz düzeltilmedi)
**Etki alanı:** `app/services/ai_nutrition.py` → `_extract_categorized_items`, çağıran `app/blueprints/menu.py` → `analyze_menu`
**Önem:** Yüksek — büyük menüler **tamamen** boş dönebilir.

## Özet

PR #32, menü çıkarımının **girişini** (10 000 → 40 000 karakter) ve **öğe
kapasitesini** (50 → 80) büyüttü, böylece uzun menülerde (örn. ~32 k karakterlik
BigChefs sayfası) sonradan gelen kategoriler (Burgerler, Pizzalar, ana yemekler,
tatlılar) LLM'e ulaşmadan kesilmesin. Ancak LLM çağrısının **çıkış** sınırı
`max_tokens=2500` olduğu gibi bırakıldı.

Sonuç: model artık çok daha fazla giriş alıp çok daha fazla öğe (≤80) döndürmeye
çalışıyor, ama yanıtı 2500 token'da kesilebiliyor. Çıkış JSON'u dizi ortasında
kesilirse, parça parça değil **tamamen** kullanılamaz hale geliyor.

## Neden tamamen başarısız oluyor?

`_extract_categorized_items` içindeki ayrıştırıcı (yaklaşık):

```python
raw  = _openai_chat(..., max_tokens=2500)      # çıkış burada kesilebilir
start = raw.find("{")
end   = raw.rfind("}") + 1
parsed = json.loads(raw[start:end])            # kesik JSON -> JSONDecodeError
...
except json.JSONDecodeError:
    return {}                                   # TÜM menü düşer
```

Kesik bir yanıt (örn. `...{"categories": {"Tatlılar": ["Sufle", "Profit`)
geçerli bir kapanış parantezi içermez; `rfind("}")` yanlış/iç bir paranteze
düşer ya da `json.loads` hata verir. Her iki durumda da fonksiyon `{}` döner.

`analyze_menu` (`app/blueprints/menu.py`) bunu boş çıkarım olarak görür ve
kullanıcıya `OUTPUT_PARSING_FAILED` döndürür:

```python
if not all_items:
    return jsonify({"success": False, "error": "OUTPUT_PARSING_FAILED", ...}), 200
```

`framework_state` olmadan bir kez yeniden denese de (aynı uzun menü, aynı çıkış
sınırı) sonuç değişmez — sorun girişte değil, **çıkış token bütçesinde**.

## Somut senaryo

1. Kullanıcı 30 kategorili, ~32 k karakterlik bir restoran menüsü tarar.
2. Giriş artık 40 k pencereyle tam olarak LLM'e ulaşır (PR #32 düzeltmesi çalışır).
3. Model "her kategoriden en az 2 + kalan kotayı doldur" istemi gereği ~70-80
   Türkçe yemek adı üretir. Türkçe sözcükler İngilizceye göre daha çok token'a
   bölündüğünden çıkış 2500 token'ı aşar.
4. OpenAI yanıtı dizi ortasında keser.
5. Ayrıştırıcı `{}` döner → `analyze_menu` `OUTPUT_PARSING_FAILED` döner.
6. **Kısmî sonuç bile yok**: PR #32'nin "eksik kategorileri kurtar" hedefinin tam
   tersine, büyük menü **hiç** sonuç vermez.

> İroni: Bu risk, tam da düzeltmenin hedeflediği büyük menülerde (uzun giriş =
> uzun çıkış) en olası durumdur. Düzeltme, "kısmî kapsama" sorununu "topyekûn
> başarısızlık" riskine çevirebilir.

## Önerilen düzeltmeler (öncelik sırasıyla)

1. **`max_tokens`'i öğe kapasitesiyle orantılı büyüt.** 80 öğe + kategori adları
   + JSON yapısı için ~4000-6000 daha güvenli bir tavandır. İdeali, `MAX_MENU_ITEMS`
   gibi tek bir kaynaktan türetmek (kapasite ile çıkış bütçesini birlikte tutmak).
2. **Kesik JSON'a dayanıklı ayrıştırma.** `json.loads` başarısızsa, son tamamlanmış
   kategori/öğe sınırına kadar olan kısmı kurtarmayı dene (en azından kısmî sonuç
   dönsün, `{}` yerine).
3. **`finish_reason == "length"` tespiti + log.** OpenAI yanıtında kesilme sinyali
   varsa açıkça logla; sessiz `{}` yerine ölçülebilir bir uyarı üret.
4. **Kapasite/çıkış ilişkisini test et.** İdealde `max_tokens`'in `MAX_MENU_ITEMS`
   ile birlikte hareket ettiğini doğrulayan bir regresyon testi (şu an yok).

## İlgili kod

| Konu | Yer |
| --- | --- |
| Sabit `max_tokens=2500` | `app/services/ai_nutrition.py` (`_extract_categorized_items` içindeki `_openai_chat` çağrısı) |
| 80 öğe kapasitesi | `app/blueprints/menu.py` `MAX_MENU_ITEMS = 80` + istem metni "toplam en fazla 80 yemek" |
| 40 k giriş penceresi | `app/services/ai_nutrition.py` `_MENU_EXTRACT_MAX_CHARS = 40000` |
| Sessiz `{}` dönüşü | `_extract_categorized_items` `except json.JSONDecodeError: return {}` |
| Kullanıcıya hata | `app/blueprints/menu.py` `OUTPUT_PARSING_FAILED` |

## Test durumu

Bu yol şu an **birim testiyle kapsanmıyor** (deterministik değil; canlı LLM çıktısı
gerektirir). Çıkarım giriş penceresi `tests/test_menu_extract_window.py` ile
pinlendi, ancak çıkış token bütçesi/kesilme yolu kapsanmıyor. Düzeltme uygulanınca
(öneri #1/#2) kapasite-çıkış ilişkisi için bir regresyon testi eklenmeli.
