"""First-party candidate only. Approval never comes from an env/client flag.

Separate from D1 and Article 9. Changing these bytes requires a new version.
"""
import hashlib
from types import MappingProxyType

PURPOSE = "first_party.health_fitness"
VERSION = "health-fitness-v1-review"
APPROVED_VERSION = None  # Founder/privacy review and legal substantiation pending.
TEXTS = MappingProxyType({
    "en": (
        "I explicitly consent to AxisAI processing the health information I choose to provide "
        "to deliver my selected fitness and nutrition tracking, calculations, plans and history. "
        "This includes body measurements and weight, injuries or medical limitations, recovery "
        "and check-in answers, food, water and supplement records, workouts, and health information "
        "in my notes or selected photos. Consent is optional. If I decline or withdraw, these "
        "health features stop; account controls and available non-health features remain accessible. "
        "I can withdraw in Settings. Withdrawal stops further consent-based health use; it does "
        "not automatically delete existing records. I can request deletion or delete my account. "
        "Existing records are not authorised retroactively; if I consent, their use for this "
        "purpose can resume from now. This does not grant AI-provider sharing permission or "
        "establish an international-transfer arrangement. Those are separate. Read the Privacy Notice."
    ),
    "tr": (
        "Seçtiğim fitness ve beslenme takibi, hesaplamalar, planlar ve geçmiş özelliklerini "
        "sunmak için paylaşmayı seçtiğim sağlık bilgilerimin AxisAI tarafından işlenmesine açık "
        "rıza veriyorum. Buna vücut ölçümleri ve kilo, sakatlık veya tıbbi kısıtlar, toparlanma "
        "ve kontrol yanıtları, yiyecek, su ve takviye kayıtları, antrenmanlar ile notlarımda veya "
        "seçtiğim fotoğraflarda bulunan sağlık bilgileri dahildir. Rıza isteğe bağlıdır. Rıza "
        "vermezsem veya geri alırsam bu sağlık özellikleri durur; hesap kontrolleri ve kullanılabilir "
        "sağlık dışı özellikler erişilebilir kalır. Rızamı Ayarlar'dan geri alabilirim. Geri alma, "
        "rızaya dayalı sonraki sağlık verisi kullanımını durdurur; mevcut kayıtları kendiliğinden "
        "silmez. Silme talep edebilir veya hesabımı silebilirim. Mevcut kayıtlara geriye dönük "
        "yetki verilmez; rıza verirsem bu amaçla kullanımları bundan sonra devam edebilir. Bu rıza, "
        "yapay zekâ sağlayıcılarıyla paylaşım izni vermez veya yurt dışı aktarım düzenlemesi "
        "oluşturmaz. Bunlar ayrıdır. Gizlilik ve Aydınlatma Metni'ni okuyun."
    ),
})
DIGESTS = MappingProxyType({locale: hashlib.sha256(text.encode("utf-8")).hexdigest()
                          for locale, text in TEXTS.items()})


def published():
    if APPROVED_VERSION != VERSION:
        return None
    return {"purpose": PURPOSE, "version": VERSION, "texts": dict(TEXTS),
            "text_digests": dict(DIGESTS)}
