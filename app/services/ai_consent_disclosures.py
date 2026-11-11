"""Source-owned disclosure text projection for D1, never a policy authority.

POLICIES in ai_consent remains the only authority. This source candidate
projects the existing R1/R2 Training texts only. Production deployment and
activation remain separate. Environment and request values cannot publish
text or qualify a policy.
"""
from hashlib import sha256
from types import MappingProxyType

from app.services import ai_consent


# (version, locale) -> exact, reviewed UTF-8 text, without newline normalization.
PUBLISHED_TEXTS = MappingProxyType({
    ('d1-closure01-training-candidate', 'en'): (
        'Allow AxisAI to send the information needed to create your AI training plan to '
        'Amazon Web Services’ (AWS) Amazon Bedrock service for processing using Claude models'
        ' developed by Anthropic? This includes your goal and fitness level, age and weight, '
        'requested frequency, equipment, focus, duration and training/cardio preferences, '
        'relevant injury or health constraints and derived movement restrictions, risk flags,'
        ' and bounded recovery/progression guidance. These facts can include health-related '
        'information. Repairs or retries needed to produce a valid plan may send this '
        'information again to an authorized model.\n'
        '\n'
        'This permission covers training-plan generation only. It does not permit Coach '
        'conversation, nutrition-plan generation, photo analysis or optional AI Memory. '
        'Reading your existing plan or logging a workout does not itself grant AI permission.'
        ' You can decline and continue available workout logging, ordinary progress tracking '
        'and other non-AI features; a non-AI general plan is available only where enabled in '
        'the released app.\n'
        '\n'
        'Withdraw training-plan permission in Settings → AI sharing permissions → Training. '
        'Confirmed withdrawal blocks new authorized processing and pending protected plan '
        'publication or confirmation under the old permission. Already-transmitted requests '
        'cannot be recalled. Existing saved plans and training records are not deleted by '
        'withdrawal; account deletion is separate in Settings. AI plans may be unsuitable or '
        'wrong. This is general fitness guidance, not medical advice or a guarantee of safety'
        ' or results. Stop if you feel unwell and seek appropriate professional advice. AI-'
        'generated content may be inaccurate, incomplete, misleading or out of date. Check '
        'important information independently before relying on it.\n'
        '\n'
        'For this operation, information is received by AWS’s Amazon Bedrock service; content'
        ' is not sent directly to Anthropic. Processing may take place outside Türkiye in one'
        ' of the AWS EU Regions listed in section 5 of the Privacy Notice. See section 5 for '
        'recipients and processing locations, section 9 for the international-transfer '
        'arrangement, section 10 for retention and deletion of records and provider copies, '
        'and section 11 for your rights. You can read this information before deciding: '
        'https://www.axisaiapp.com/privacy. This permission covers only the operation '
        'described above; it does not authorize other AI features or the use of data outside '
        'their permitted scope.\n'
    ),
    ('d1-closure01-training-candidate', 'tr'): (
        "AxisAI'ın AI antrenman planınızı oluşturmak için gereken bilgileri Amazon Web "
        'Services’in (AWS) Amazon Bedrock hizmetine, Anthropic tarafından geliştirilen Claude'
        ' modelleriyle işlenmek üzere göndermesine izin veriyor musunuz? Hedefiniz ve fitness'
        ' düzeyiniz, yaşınız ve kilonuz, istediğiniz sıklık, ekipman, odak, süre ve '
        'antrenman/kardiyo tercihleri; ilgili sakatlık veya sağlık kısıtları ve bunlardan '
        'çıkarılan hareket sınırlamaları, risk işaretleri ve sınırlı toparlanma/ilerleme '
        'rehberliği bu kapsamdadır. Bunlar sağlıkla ilgili bilgiler içerebilir. Geçerli bir '
        'plan üretmek için gereken düzeltme veya yeniden denemelerde bu bilgiler yetkili bir '
        'modele tekrar gönderilebilir.\n'
        '\n'
        'Bu izin yalnızca antrenman planı oluşturmayı kapsar. Koç konuşmasını, beslenme planı'
        " oluşturmayı, fotoğraf analizini veya isteğe bağlı AI Hafıza'yı kapsamaz. Mevcut "
        'planı görüntülemek veya antrenman kaydetmek AI izni vermez. İzin vermeden '
        'kullanılabilir antrenman kaydı, olağan ilerleme takibi ve diğer AI dışı özellikleri '
        'kullanabilirsiniz; AI dışı genel plan yalnızca yayımlanan uygulamada etkinse '
        'kullanılabilir.\n'
        '\n'
        'Antrenman planı iznini Ayarlar → Yapay zekâ paylaşım izinleri → Antrenman yolundan '
        'geri çekebilirsiniz. Onaylanan geri çekme, eski izne dayalı yeni yetkili işlemeyi ve'
        ' bekleyen korumalı planın yayımlanmasını veya onaylanmasını engeller. Önceden '
        'gönderilen istekler geri çağrılamaz. İzni geri çekmek kayıtlı planları ve antrenman '
        "kayıtlarını silmez; hesap silme Ayarlar'da ayrı bir işlemdir. AI planları size uygun"
        ' olmayabilir veya hatalı olabilir. Genel fitness rehberliği sunar; tıbbi tavsiye, '
        'güvenlik veya sonuç garantisi değildir. Kendinizi kötü hissederseniz durun ve uygun '
        'bir uzmana danışın. Yapay zekâ tarafından üretilen içerik hatalı, eksik, yanıltıcı '
        'veya güncel olmayabilir. Önemli bilgilere güvenmeden önce bağımsız olarak '
        'doğrulayın.\n'
        '\n'
        'Bu işlemde bilgilerin alıcısı AWS’nin Amazon Bedrock hizmetidir; içerik doğrudan '
        'Anthropic’e gönderilmez. İşleme, Türkiye dışında, Gizlilik ve Aydınlatma Metni’nin '
        '5. bölümünde listelenen AWS AB bölgelerinden birinde gerçekleşebilir. Alıcılar ve '
        'işleme yerleri için 5. bölüme, yurt dışı aktarım düzenlemesi için 9. bölüme, '
        'kayıtların ve sağlayıcı kopyalarının saklanması ve silinmesi için 10. bölüme, '
        'haklarınız için 11. bölüme bakın. Bu bilgileri karar vermeden önce okuyabilirsiniz: '
        'https://www.axisaiapp.com/privacy. Bu izin yalnızca yukarıda açıklanan işlem '
        'içindir; diğer yapay zekâ özelliklerini veya onların kullanamadığı verileri kullanma'
        ' izni vermez.\n'
    ),
})
# Exact active versions, selected only by reviewed source after approval.
# Historical contracts may remain in POLICIES but cannot open new launch work.
LAUNCH_VERSIONS = MappingProxyType({
    "training.plan": "d1-closure01-training-candidate",
})
LOCALES = frozenset({"en", "tr"})


def localized_texts(policy):
    """Return an EN/TR pair only when both match the immutable D1 contract.

    No fallback to another language, draft version, remote page or runtime
    setting. A partial/misconfigured release offers no affirmative action.
    """
    if ai_consent.POLICIES.get(policy.version) != policy:
        return None
    expected = dict(policy.texts)
    if set(expected) != LOCALES or len(policy.texts) != len(LOCALES):
        return None
    texts = {}
    for locale in sorted(LOCALES):
        body = PUBLISHED_TEXTS.get((policy.version, locale))
        if (not isinstance(body, str) or not body.strip()
                or sha256(body.encode("utf-8")).hexdigest() != expected[locale]):
            return None
        texts[locale] = body
    return texts


def available_disclosures():
    """D1's public metadata plus the exact reviewed text a client must display.

    Used by ai_consent._public; grants independently verify this same pair.
    This helper cannot grant, authorize dispatch, or bypass the endpoint gate.
    """
    disclosures = []
    for policy in ai_consent.POLICIES.values():
        if LAUNCH_VERSIONS and LAUNCH_VERSIONS.get(policy.purpose) != policy.version:
            continue
        texts = localized_texts(policy)
        if texts is None:
            continue
        disclosures.append({
            "version": policy.version,
            "purpose": policy.purpose,
            "categories": list(policy.categories),
            "recipients": [list(recipient) for recipient in policy.recipients],
            "external_scopes": list(policy.external_scopes),
            "contract_digest": ai_consent.contract_digest(policy),
            "texts": dict(policy.texts),
            "localized_texts": texts,
        })
    return disclosures
