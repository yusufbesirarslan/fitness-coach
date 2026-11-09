"""First-party candidate only. Approval never comes from an env/client flag.

Separate from D1 and Article 9. Changing these bytes requires a new version.
"""
import hashlib
from types import MappingProxyType

PURPOSE = "first_party.health_fitness"
VERSION = "health-fitness-v2-review"  # v2: A1 = O2-B withdrawal erasure
APPROVED_VERSION = None  # Founder/privacy review and legal substantiation pending.
TEXTS = MappingProxyType({
    "en": (
        "I explicitly consent to AxisAI processing the health information I choose to provide "
        "to deliver my selected fitness and nutrition tracking, calculations, plans and history. "
        "This includes body measurements and weight, injuries or medical limitations, recovery "
        "and check-in answers, food, water and supplement records, workouts, and health information "
        "in my notes or selected photos. Consent is optional. If I decline or withdraw, these "
        "health features stop; account controls and available non-health features remain accessible. "
        "I can withdraw in Settings. When I withdraw, these health features stop immediately and "
        "my health records are deleted within 30 days, unless a specific record is needed for a "
        "legal claim or request, in which case it is kept only for that purpose and only as long "
        "as needed. Deleted records are not restored if I consent again. My account and its "
        "non-health records remain. I can also delete my account. "
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
        "Rızamı Ayarlar'dan geri alabilirim. Geri "
        "aldığımda bu sağlık özellikleri hemen durur ve sağlık kayıtlarım 30 gün içinde silinir; "
        "belirli bir kayıt bir hukuki talep veya başvuru için gerekliyse yalnızca bu amaçla ve "
        "gerektiği süre boyunca saklanır. Yeniden rıza versem de silinen kayıtlar geri getirilmez. "
        "Hesabım ve sağlıkla ilgili olmayan kayıtlarım kalır. Hesabımı da silebilirim. Mevcut kayıtlara geriye dönük "
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
