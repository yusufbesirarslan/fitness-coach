"""Deterministik sakatlık/tıbbi-durum kontrendikasyon motoru.

Antrenman planı üreten LLM'e GÜVENİLİR, sabit klinik kısıtlar enjekte eder:
hangi hareketler YASAK, hangi güvenli alternatifler önerilmeli, hangi odak
vurgulanmalı. LLM'in metni doğru yorumlamasına bel bağlamak yerine kontrendikasyon
bilgisini KOD'da tutarız (tek doğruluk kaynağı) ve iki yere de aynı motordan besleriz:
  - app/blueprints/training.py  (form ile plan üretimi)
  - app/services/ai_coach.py    (sohbet koçu bağlamı)

Tasarım kuralları:
- Eşleştirme TR + EN takma adlarla, normalize edilmiş (küçük harf, TR karakter) alt-dize
  araması ile yapılır → "sağ menisküs yırtığı", "meniscus tear" hepsi yakalanır.
- build_injury_directive(text): LLM istemi için katı, yapısal direktif bloğu.
- banned_exercise_names(text): üretilen plandaki egzersiz adlarını süzmek için
  kanonik yasak-hareket parçacıkları kümesi (savunma derinliği / son ağ).
- Boş/None/"hiçbiri/yok/none" girdileri GÜVENLE ele alınır (asla patlamaz, boş döner) —
  sakatlık yoksa jeneratör normal planlar.
"""
import re
import unicodedata


# "Sakatlık yok" anlamına gelen değerler — bunlar kısıt ÜRETMEZ (normal planla).
_NONE_TOKENS = {"", "hicbiri", "hicbir", "yok", "none", "no", "hayir", "saglikli",
                "sorun yok", "bir sey yok", "n/a", "na", "-"}


def _normalize(text):
    """Küçük harfe indir + Türkçe aksanları sadeleştir (menisküs→meniskus,
    kifoz→kifoz, ağrı→agri) → aksandan bağımsız eşleştirme. Asla istisna atmaz."""
    if not text:
        return ""
    s = str(text).strip().lower()
    # Türkçe'ye özgü dönüşümler (NFKD bazıları için yeterli değil: ı, ş, ğ).
    s = (s.replace("ı", "i").replace("İ", "i").replace("ş", "s")
         .replace("ğ", "g").replace("ç", "c").replace("ö", "o").replace("ü", "u"))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s


# ── Klinik kontrendikasyon bilgi tabanı ──────────────────────────────────────
# Her kayıt bir durumdur. Alanlar:
#   label    : kullanıcıya/LLM'e gösterilecek Türkçe ad
#   aliases  : normalize edilmiş eşleşme parçacıkları (TR + EN)
#   banned   : YASAK hareket adı parçacıkları (normalize, alt-dize) — hem direktif
#              metnine hem de post-filtre kümesine girer
#   safe     : güvenli alternatif/öneri egzersizler (Türkçe sunum)
#   focus    : vurgulanması gereken odak (mobilite, stabilite, güçlendirme)
# Sıra SABİT tutulur (prompt cache + deterministik çıktı).
_CONDITIONS = [
    {
        "key": "meniscus",
        "label": "Menisküs yırtığı / diz",
        "aliases": ["meniskus", "meniscus", "menisk"],
        "banned": ["deep squat", "derin squat", "full squat", "agir squat",
                   "barbell squat", "back squat", "jump squat", "box jump",
                   "jump", "siçrama", "sicrama", "plyo", "lunge", "hack squat",
                   "pistol squat"],
        "safe": ["Box Squat (kontrollü, sınırlı ROM)", "Leg Press (güvenli ROM)",
                 "Leg Extension (hafif, tam kilitlemesiz)", "Leg Curl (hamstring)",
                 "Glute Bridge / Hip Thrust", "düşük-darbe kardiyo (bisiklet, yüzme)"],
        "focus": "düşük darbe; diz ekleminde derin fleksiyon ve eksenel sıçramadan kaçın",
    },
    {
        "key": "knee",
        "label": "Diz ağrısı / sakatlığı",
        "aliases": ["diz", "knee", "patella", "acl", "on capraz", "kondromalazi",
                    "chondro"],
        "banned": ["deep squat", "derin squat", "full squat", "jump squat",
                   "box jump", "jump", "plyo", "siçrama", "sicrama", "lunge",
                   "pistol squat", "hack squat"],
        "safe": ["Box Squat (sınırlı ROM)", "Leg Press (güvenli ROM)",
                 "Leg Curl", "Glute Bridge / Hip Thrust", "Step-Up (alçak)",
                 "düşük-darbe kardiyo"],
        "focus": "düşük darbe; ağrısız ROM içinde çalış, tam derin çömelme yok",
    },
    {
        "key": "kyphosis",
        "label": "Kifoz (kamburluk / torasik)",
        "aliases": ["kifoz", "kyphosis", "kambur", "kambır", "hunchback"],
        "banned": ["heavy bench", "agir bench", "decline bench", "behind neck",
                   "ense", "heavy overhead", "agir overhead", "agir omuz pres",
                   "upright row", "dips (gogus)", "wide grip bench"],
        "safe": ["Face Pull", "Band Pull-Apart", "Chest-Supported Row",
                 "Prone Y-T-W Raise", "Rear Delt Fly", "Incline Press (kontrollü)",
                 "torasik mobilite (foam roller, cat-camel)"],
        "focus": "torasik mobilite + üst sırt güçlendirme; aşırı göğüs-baskın itişi "
                 "ve ağır overhead'i SINIRLA, ekstansiyon/çekiş hacmini artır",
    },
    {
        "key": "scoliosis",
        "label": "Skolyoz (omurga eğriliği)",
        "aliases": ["skolyoz", "scoliosis", "omurga egrili", "spinal egri"],
        "banned": ["heavy back squat", "agir back squat", "barbell back squat",
                   "heavy deadlift", "agir deadlift", "good morning",
                   "tek kol agir", "behind neck", "ense", "overhead press (agir)"],
        "safe": ["Leg Press", "Chest-Supported Row", "Machine Press (simetrik)",
                 "Bird Dog", "Dead Bug", "Plank / Side Plank (dengeli)",
                 "kontrollü çift-taraflı (bilateral) makine işi"],
        "focus": "core stabilitesi + SİMETRİK yüklenme; ağır eksenel yük ve "
                 "tek-taraflı dengesiz ağır yüklemeden kaçın",
    },
    {
        "key": "lumbar_herniation",
        "label": "Bel fıtığı / lomber disk",
        # "fıtık" çekimleri normalize sonrası 'fitik' (fıtık) / 'fitig' (fıtığı) olur —
        # ikisini de yakala, yoksa 'bel fıtığı' yalnızca yumuşak 'bel' kuralına düşer.
        "aliases": ["bel fitik", "fitik", "fitig", "herni", "herniation", "lomber",
                    "lumbar", "disk", "bel disk", "siyatik", "sciatica", "bulging disc"],
        "banned": ["deadlift", "back squat", "agir squat", "good morning",
                   "bent over row", "barbell row", "agir egilme", "sit-up", "situp",
                   "mekik", "russian twist", "rus twist", "leg raise (asili)",
                   "hanging leg raise", "behind neck", "ense"],
        "safe": ["Bird Dog", "Side Plank", "Curl-Up (McGill)", "Glute Bridge",
                 "Hip Thrust", "Leg Press (nötr bel, destekli)",
                 "Chest-Supported Row", "Cable Row (nötr omurga)"],
        "focus": "nötr omurga + core endurance (McGill Big 3); ağır eksenel spinal "
                 "yük ve yük altında bel fleksiyonu/rotasyonundan KESİNLİKLE kaçın",
    },
    {
        "key": "lower_back",
        "label": "Bel ağrısı",
        "aliases": ["bel agri", "bel agrisi", "lower back", "low back", "bel"],
        "banned": ["heavy deadlift", "agir deadlift", "good morning",
                   "heavy bent over row", "agir bent over", "sit-up", "situp",
                   "mekik", "russian twist", "rus twist"],
        "safe": ["Bird Dog", "Side Plank", "Glute Bridge", "Hip Thrust",
                 "Chest-Supported Row", "Leg Press (destekli)",
                 "Cable Row (nötr omurga)"],
        "focus": "nötr omurga + core stabilite; ağır hip-hinge ve yük altında bel "
                 "fleksiyonunu sınırla",
    },
    {
        "key": "shoulder",
        "label": "Omuz sakatlığı (sıkışma/rotator manşet)",
        "aliases": ["omuz", "shoulder", "rotator", "manset", "impingement",
                    "sikisma", "subakromial", "slap"],
        "banned": ["behind neck", "ense", "heavy overhead", "agir overhead",
                   "agir omuz pres", "upright row", "wide grip bench",
                   "geniş tutuş bench", "dips (derin)", "deep dips"],
        "safe": ["Neutral-Grip Dumbbell Press", "Landmine Press", "Face Pull",
                 "Scapular Retraction / Wall Slide", "Lateral Raise (kontrollü, hafif)",
                 "Cable Row"],
        "focus": "skapula stabilitesi + rotator manşet; ağrısız ROM, nötr tutuş; "
                 "ağır overhead ve dik çekiş (upright row) YASAK",
    },
    {
        "key": "wrist",
        "label": "Bilek sakatlığı",
        "aliases": ["bilek", "wrist", "el bilek", "karpal", "carpal"],
        "banned": ["barbell curl (duz bar)", "duz bar curl", "straight bar",
                   "heavy push-up", "agir sinav", "agir bilek"],
        "safe": ["Neutral-Grip (Hammer) hareketler", "Dumbbell varyasyonları",
                 "Cable/Machine (bilek dostu)", "Lifting Strap kullan"],
        "focus": "nötr bilek pozisyonu; ağır düz-bar baskı/curl yerine dambıl/makine",
    },
    {
        "key": "elbow",
        "label": "Dirsek sakatlığı (tenisçi/golfçü dirseği)",
        "aliases": ["dirsek", "elbow", "tenisci", "golfcu", "tennis elbow",
                    "epikondil", "epicondyl"],
        "banned": ["heavy barbell curl", "agir barbell curl", "skull crusher",
                   "skullcrusher", "agir tutus", "heavy grip"],
        "safe": ["Neutral-Grip (Hammer) Curl (hafif)", "Cable hareketler",
                 "yüksek tekrar / düşük yük", "izometrik bilek güçlendirme"],
        "focus": "kavrama yükünü azalt; yüksek tekrar-düşük ağırlık, nötr tutuş",
    },
    {
        "key": "ankle",
        "label": "Ayak bileği sakatlığı",
        "aliases": ["ayak bilek", "ankle", "asil", "achilles", "burkulma", "sprain"],
        "banned": ["box jump", "jump", "siçrama", "sicrama", "plyo", "jump rope",
                   "ip atlama", "kosu (yuksek darbe)", "sprint"],
        "safe": ["Seated Calf Raise (kontrollü)", "Leg Press Calf",
                 "bisiklet / yüzme (düşük darbe)", "denge/proprioception çalışması"],
        "focus": "düşük darbe; sıçrama ve yüksek-darbeli koşu yok",
    },
    {
        "key": "hip",
        "label": "Kalça sakatlığı (impingement/FAI)",
        "aliases": ["kalca", "hip", "fai", "impingement (kalca)", "labrum"],
        "banned": ["deep squat", "derin squat", "full squat", "agir squat",
                   "deep leg press", "derin leg press"],
        "safe": ["Box Squat (sınırlı ROM)", "Glute Bridge / Hip Thrust",
                 "Leg Press (güvenli ROM)", "Cable Kickback", "Clamshell / Band Walk"],
        "focus": "ağrısız ROM; derin kalça fleksiyonundan kaçın, glute aktivasyonu",
    },
    {
        "key": "neck",
        "label": "Boyun / servikal sorun",
        "aliases": ["boyun", "neck", "servikal", "cervical", "ense fitik"],
        "banned": ["behind neck", "ense", "heavy shrug", "agir shrug",
                   "heavy overhead", "agir overhead", "bridge (boyun)"],
        "safe": ["Face Pull (kontrollü)", "hafif Shrug (yüksek tekrar)",
                 "Chin Tuck / boyun mobilitesi", "destekli makine omuz işi"],
        "focus": "boyna eksenel/aşırı yük yok; kontrollü, hafif, yüksek-tekrar",
    },
]


def _match_conditions(text):
    """Normalize edilmiş metinde geçen tüm durumları (sırasıyla) döndür.
    Eşleşme yoksa boş liste. Asla istisna atmaz."""
    norm = _normalize(text)
    if not norm or norm in _NONE_TOKENS:
        return []
    # "Hiçbiri" gibi tek-token red değerleri (normalize sonrası) → kısıt yok.
    if norm.replace(" ", "") in {t.replace(" ", "") for t in _NONE_TOKENS}:
        return []
    matched = []
    for cond in _CONDITIONS:
        if any(alias and alias in norm for alias in cond["aliases"]):
            matched.append(cond)
    return matched


def has_constraints(text):
    """Bu sakatlık metni gerçek bir kısıt üretiyor mu? (UI/log için kolaylık)."""
    return bool(_match_conditions(text))


def build_injury_directive(text):
    """LLM istemine eklenecek KATI, yapısal sakatlık direktifi (Türkçe).

    Tanınan bir durum yoksa ama metin doluysa (ör. nadir/serbest girdi), yine de
    genel bir güvenli-uyarlama uyarısı döndürülür — sakatlık verisi sessizce
    yutulmaz. Hiç/boş/"yok" girdisinde boş string döner (jeneratör normal planlar).
    """
    norm = _normalize(text)
    if not norm or norm in _NONE_TOKENS:
        return ""

    matched = _match_conditions(text)
    header = (
        "\n═══ SAKATLIK / SAĞLIK KONTRENDİKASYONLARI (ZORUNLU — KATI UYGULA) ═══\n"
        f"- Kullanıcının bildirdiği durum(lar): {str(text).strip()[:300]}\n"
        "- Aşağıdaki YASAK hareketleri programa ASLA ekleme; güvenli alternatiflerle "
        "değiştir. Hacim ve şiddeti durumu tamamen koruyacak şekilde uyarla.\n"
    )

    if not matched:
        # Tanınmayan ama dolu girdi: güvenli tarafta kal.
        return header + (
            "- Bu durumu tetikleyebilecek ağır eksenel yük, yüksek-darbeli ve uç-ROM "
            "hareketlerden kaçın; düşük-darbeli, kontrollü, destekli varyasyonları tercih et.\n"
            "- İlgili egzersizin 'not' alanına kısa bir sakatlık-güvenliği ipucu ekle.\n"
        )

    blocks = [header]
    for cond in matched:
        blocks.append(
            f"\n▸ {cond['label']}:\n"
            f"  • YASAK: {', '.join(cond['banned'][:10])}\n"
            f"  • GÜVENLİ ALTERNATİF: {', '.join(cond['safe'])}\n"
            f"  • ODAK: {cond['focus']}\n"
        )
    blocks.append(
        "\n- Yukarıdaki güvenli alternatifleri ilgili antrenman günlerinde KULLAN.\n"
        "- Kontrendike bir hareketi koyduğun egzersizin 'not' alanına neden güvenli "
        "olduğunu/uyarlamayı yaz.\n"
    )
    return "".join(blocks)


def banned_exercise_names(text):
    """Eşleşen durumların TÜM yasak-hareket parçacıklarının kümesi (normalize, küçük
    harf). Üretilen plandaki egzersiz adlarını süzmek için kullanılır (son güvenlik
    ağı). Hiç kısıt yoksa boş küme."""
    out = set()
    for cond in _match_conditions(text):
        for b in cond["banned"]:
            nb = _normalize(b)
            # Çok genel/tek-kelime gürültüsünü ele ('jump' kalsın ama parantezli
            # açıklamalar normalize edilince sadeleşir). Boş olanları atla.
            if nb:
                out.add(nb)
    return out


# Post-filtrede kullanılan, gerçek yasak çekirdek terimler (parantezli açıklama veya
# "agir/heavy" öneki olmadan da egzersiz adında geçebilecek olanlar). Bunlar egzersiz
# adında alt-dize olarak aranır.
def find_contraindicated(exercise_name, text):
    """`exercise_name` verilen sakatlıklara göre kontrendike mi? Eşleşen yasak
    parçacığını döndürür, yoksa None. Hem TR hem EN adları yakalamak için normalize
    karşılaştırır. Sadece anlamlı (>=3 karakter, parantezsiz) çekirdek terimleri
    dikkate alır → 'Leg Extension' gibi güvenli adların yanlış eşleşmesini önler."""
    norm_ex = _normalize(exercise_name)
    if not norm_ex:
        return None
    for cond in _match_conditions(text):
        for b in cond["banned"]:
            core = _normalize(b).split("(")[0].strip()
            # Tek-kelime ve çok kısa terimler (ör. 'bel', 'jump') ad içinde gerçek
            # hareketi gösterir; ancak gürültüyü azaltmak için >=3 karakter ara.
            if len(core) >= 3 and core in norm_ex:
                return b
    return None
