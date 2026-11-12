"""Source-owned disclosure text projection for D1, never a policy authority.

POLICIES in ai_consent remains the only authority. This source candidate
projects the existing R1/R2 Training texts and the founder-content-approved
(2026-11-12) Coach, Nutrition plan, Pump Check and QR Menu texts. Founder
content approval is not legal counsel sign-off. Production deployment and
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
    ('d1-closure02-coach-candidate-r2', 'en'): (
        'Allow AxisAI to send the information needed to answer your Coach messages to Amazon '
        'Web Services’ (AWS) Amazon Bedrock service for processing using Claude models '
        'developed by Anthropic? This includes your messages and recent conversation and the '
        'account information relevant to them: your preferences and goals, training history and '
        'current workout, nutrition activity and targets, body measurements, recovery ratings, '
        'injury or health limitations, the results of actions Coach performs for you, and an '
        'image only where the released app lets you share one with Coach. These facts can '
        'include health-related information. When you ask, Coach can read your records, '
        'estimate a food you describe, review a shared image where that is available, and '
        'prepare a meal or workout log that is saved only after you confirm it. Retries needed '
        'to produce a valid answer may send this information again to an authorized model.\n\n'
        'This permission covers Coach conversation only, including the food estimates and image '
        'review Coach performs inside a conversation. It does not permit training-plan or '
        'nutrition-plan generation, Pump Check, QR Menu analysis or optional AI Memory. Opening '
        'Coach or reading earlier messages does not itself grant AI permission. You can decline '
        'and continue workout and food logging, ordinary progress tracking and other features '
        'that do not need Coach.\n\nWithdraw Coach permission in Settings → AI sharing '
        'permissions → Coach. Confirmed withdrawal blocks new authorized Coach processing and '
        'any pending Coach action under the old permission. Already-transmitted requests cannot '
        'be recalled. Withdrawal does not delete your Coach history or records you confirmed; '
        'account deletion is separate in Settings, and withdrawing the separate health data '
        'permission deletes health records, including Coach history, as described in section 4 '
        'of the Privacy Notice. Coach replies are general fitness and nutrition information, '
        'not medical advice, diagnosis or treatment, or a guarantee of safety or results. Stop '
        'if you feel unwell and seek appropriate professional advice. AI-generated content may '
        'be inaccurate, incomplete, misleading or out of date. Check important information '
        'independently before relying on it.\n\nFor this operation, information is received by '
        'AWS’s Amazon Bedrock service; content is not sent directly to Anthropic. Processing '
        'may take place outside Türkiye in one of the AWS EU Regions listed in section 5 of the '
        'Privacy Notice. See section 5 for recipients and processing locations, section 9 for '
        'the international-transfer arrangement, section 10 for retention and deletion of '
        'records and provider copies, and section 11 for your rights. You can read this '
        'information before deciding: https://www.axisaiapp.com/privacy. This permission covers '
        'only the operation described above; it does not authorize other AI features or the use '
        'of data outside their permitted scope.\n'
    ),
    ('d1-closure02-coach-candidate-r2', 'tr'): (
        "AxisAI'ın Koç mesajlarınızı yanıtlamak için gereken bilgileri Amazon Web Services’in "
        '(AWS) Amazon Bedrock hizmetine, Anthropic tarafından geliştirilen Claude modelleriyle '
        'işlenmek üzere göndermesine izin veriyor musunuz? Mesajlarınız, son konuşma geçmişiniz '
        've bunlarla ilgili hesap bilgileriniz bu kapsamdadır: tercihleriniz ve hedefleriniz, '
        'antrenman geçmişiniz ve devam eden antrenmanınız, beslenme kayıtlarınız ve '
        'hedefleriniz, vücut ölçüleriniz, toparlanma değerlendirmeleriniz, sakatlık veya sağlık '
        "kısıtlarınız, Koç'un sizin için yaptığı işlemlerin sonuçları ve yalnızca yayımlanan "
        "uygulama Koç'la paylaşmanıza izin veriyorsa bir görsel. Bunlar sağlıkla ilgili "
        'bilgiler içerebilir. İstediğinizde Koç kayıtlarınızı okuyabilir, tarif ettiğiniz bir '
        'yiyeceği tahmin edebilir, bu özellik varsa paylaştığınız bir görseli inceleyebilir ve '
        'yalnızca siz onayladıktan sonra kaydedilen bir öğün veya antrenman kaydı '
        'hazırlayabilir. Geçerli bir yanıt üretmek için gereken yeniden denemelerde bu bilgiler '
        "yetkili bir modele tekrar gönderilebilir.\n\nBu izin yalnızca Koç konuşmasını ve Koç'un "
        'bir konuşma içinde yaptığı yiyecek tahminlerini ve görsel incelemesini kapsar. '
        "Antrenman veya beslenme planı oluşturmayı, Pump Check'i, QR Menü analizini veya isteğe "
        "bağlı AI Hafıza'yı kapsamaz. Koç'u açmak veya önceki mesajları görüntülemek AI izni "
        'vermez. İzin vermeden antrenman ve yemek kaydı, olağan ilerleme takibi ve Koç '
        'gerektirmeyen diğer özellikleri kullanabilirsiniz.\n\nKoç iznini Ayarlar → Yapay zekâ '
        'paylaşım izinleri → Koç yolundan geri çekebilirsiniz. Onaylanan geri çekme, eski izne '
        'dayalı yeni yetkili Koç işlemlerini ve bekleyen Koç eylemlerini engeller. Önceden '
        'gönderilen istekler geri çağrılamaz. İzni geri çekmek Koç geçmişinizi veya '
        "onayladığınız kayıtları silmez; hesap silme Ayarlar'da ayrı bir işlemdir; ayrı sağlık "
        'verisi iznini geri çekmek ise Koç geçmişi dahil sağlık kayıtlarınızı Gizlilik ve '
        'Aydınlatma Metni’nin 4. bölümünde açıklandığı şekilde siler. Koç yanıtları genel '
        'fitness ve beslenme bilgisidir; tıbbi tavsiye, teşhis veya tedavi, güvenlik veya sonuç '
        'garantisi değildir. Kendinizi kötü hissederseniz durun ve uygun bir uzmana danışın. '
        'Yapay zekâ tarafından üretilen içerik hatalı, eksik, yanıltıcı veya güncel '
        'olmayabilir. Önemli bilgilere güvenmeden önce bağımsız olarak doğrulayın.\n\nBu işlemde '
        'bilgilerin alıcısı AWS’nin Amazon Bedrock hizmetidir; içerik doğrudan Anthropic’e '
        'gönderilmez. İşleme, Türkiye dışında, Gizlilik ve Aydınlatma Metni’nin 5. bölümünde '
        'listelenen AWS AB bölgelerinden birinde gerçekleşebilir. Alıcılar ve işleme yerleri '
        'için 5. bölüme, yurt dışı aktarım düzenlemesi için 9. bölüme, kayıtların ve sağlayıcı '
        'kopyalarının saklanması ve silinmesi için 10. bölüme, haklarınız için 11. bölüme '
        'bakın. Bu bilgileri karar vermeden önce okuyabilirsiniz: '
        'https://www.axisaiapp.com/privacy. Bu izin yalnızca yukarıda açıklanan işlem içindir; '
        'diğer yapay zekâ özelliklerini veya onların kullanamadığı verileri kullanma izni '
        'vermez.\n'
    ),
    ('d1-closure02-nutrition-plan-candidate', 'en'): (
        'Allow AxisAI to send the information needed to create your AI nutrition plan to Amazon '
        'Web Services’ (AWS) Amazon Bedrock service for processing using Claude models '
        'developed by Anthropic? This includes your nutrition targets, such as calories and '
        'macronutrients, your food preferences and the notes you give. Your raw age, gender, '
        'height and weight are not automatically included, but your targets are calculated from '
        'your body information, and your preferences and notes can mention diets, allergies or '
        'conditions. These facts can include health-related information. Repairs or retries '
        'needed to produce a valid plan may send this information again to an authorized model.\n'
        '\nThis permission covers nutrition-plan generation only. It does not permit Coach '
        'conversation, training-plan generation, photo analysis, QR Menu analysis or optional '
        'AI Memory. Reading your existing plan or logging food does not itself grant AI '
        'permission. You can decline and continue food logging, your nutrition targets and '
        'other non-AI features.\n\nWithdraw nutrition-plan permission in Settings → AI sharing '
        'permissions → Nutrition plan. Confirmed withdrawal blocks new authorized processing '
        'under the old permission. Already-transmitted requests cannot be recalled. Existing '
        'saved plans and food records are not deleted by withdrawal; account deletion is '
        'separate in Settings. AI nutrition plans may be unsuitable or wrong for you, including '
        'for allergies, intolerances or medical conditions. This is general nutrition guidance, '
        'not medical or dietetic advice or a guarantee of safety or results. Check ingredients '
        'yourself and seek appropriate professional advice if you have a health condition. '
        'AI-generated content may be inaccurate, incomplete, misleading or out of date. Check '
        'important information independently before relying on it.\n\nFor this operation, '
        'information is received by AWS’s Amazon Bedrock service; content is not sent directly '
        'to Anthropic. Processing may take place outside Türkiye in one of the AWS EU Regions '
        'listed in section 5 of the Privacy Notice. See section 5 for recipients and processing '
        'locations, section 9 for the international-transfer arrangement, section 10 for '
        'retention and deletion of records and provider copies, and section 11 for your rights. '
        'You can read this information before deciding: https://www.axisaiapp.com/privacy. This '
        'permission covers only the operation described above; it does not authorize other AI '
        'features or the use of data outside their permitted scope.\n'
    ),
    ('d1-closure02-nutrition-plan-candidate', 'tr'): (
        "AxisAI'ın AI beslenme planınızı oluşturmak için gereken bilgileri Amazon Web "
        'Services’in (AWS) Amazon Bedrock hizmetine, Anthropic tarafından geliştirilen Claude '
        'modelleriyle işlenmek üzere göndermesine izin veriyor musunuz? Kalori ve makro besin '
        'değerleri gibi beslenme hedefleriniz, yiyecek tercihleriniz ve verdiğiniz notlar bu '
        'kapsamdadır. Ham yaşınız, cinsiyetiniz, boyunuz ve kilonuz otomatik olarak dahil '
        'edilmez; ancak hedefleriniz vücut bilgilerinizden hesaplanır, tercihleriniz ve '
        'notlarınız da diyet, alerji veya rahatsızlıklardan söz edebilir. Bunlar sağlıkla '
        'ilgili bilgiler içerebilir. Geçerli bir plan üretmek için gereken düzeltme veya '
        'yeniden denemelerde bu bilgiler yetkili bir modele tekrar gönderilebilir.\n\nBu izin '
        'yalnızca beslenme planı oluşturmayı kapsar. Koç konuşmasını, antrenman planı '
        "oluşturmayı, fotoğraf analizini, QR Menü analizini veya isteğe bağlı AI Hafıza'yı "
        'kapsamaz. Mevcut planı görüntülemek veya yemek kaydetmek AI izni vermez. İzin vermeden '
        'yemek kaydı, beslenme hedefleriniz ve diğer AI dışı özellikleri kullanabilirsiniz.\n\n'
        'Beslenme planı iznini Ayarlar → Yapay zekâ paylaşım izinleri → Beslenme planı yolundan '
        'geri çekebilirsiniz. Onaylanan geri çekme, eski izne dayalı yeni yetkili işlemeyi '
        'engeller. Önceden gönderilen istekler geri çağrılamaz. İzni geri çekmek kayıtlı '
        "planları ve yemek kayıtlarını silmez; hesap silme Ayarlar'da ayrı bir işlemdir. AI "
        'beslenme planları alerjiler, intoleranslar veya rahatsızlıklar dahil size uygun '
        'olmayabilir veya hatalı olabilir. Genel beslenme rehberliği sunar; tıbbi veya '
        'diyetisyen tavsiyesi, güvenlik veya sonuç garantisi değildir. İçerikleri kendiniz '
        'kontrol edin ve bir sağlık sorununuz varsa uygun bir uzmana danışın. Yapay zekâ '
        'tarafından üretilen içerik hatalı, eksik, yanıltıcı veya güncel olmayabilir. Önemli '
        'bilgilere güvenmeden önce bağımsız olarak doğrulayın.\n\nBu işlemde bilgilerin alıcısı '
        'AWS’nin Amazon Bedrock hizmetidir; içerik doğrudan Anthropic’e gönderilmez. İşleme, '
        'Türkiye dışında, Gizlilik ve Aydınlatma Metni’nin 5. bölümünde listelenen AWS AB '
        'bölgelerinden birinde gerçekleşebilir. Alıcılar ve işleme yerleri için 5. bölüme, yurt '
        'dışı aktarım düzenlemesi için 9. bölüme, kayıtların ve sağlayıcı kopyalarının '
        'saklanması ve silinmesi için 10. bölüme, haklarınız için 11. bölüme bakın. Bu '
        'bilgileri karar vermeden önce okuyabilirsiniz: https://www.axisaiapp.com/privacy. Bu '
        'izin yalnızca yukarıda açıklanan işlem içindir; diğer yapay zekâ özelliklerini veya '
        'onların kullanamadığı verileri kullanma izni vermez.\n'
    ),
    ('d1-closure02-pump-check-candidate', 'en'): (
        'Allow AxisAI to send one photo you choose for Pump Check, together with the body '
        'region and setting you select and the description you write, to Amazon Web Services’ '
        '(AWS) Amazon Bedrock service for processing using Claude models developed by '
        'Anthropic? A body photo shows your physique and can indirectly reveal health-related '
        'or other sensitive information. Before sending, our server re-encodes the photo '
        'without embedded details such as location metadata; this does not hide what is visible '
        'in the picture. Retries needed to produce a valid result may send this information '
        'again to an authorized model.\n\nThis permission covers analysis of the single photo you '
        'submit only. It does not compare photos or measure change over time, and it does not '
        'permit Coach conversation, plan generation, QR Menu analysis or optional AI Memory. '
        'Choosing a photo or viewing your Pump Check history does not itself grant AI '
        'permission. Submit only photos of yourself that you want analysed. You can decline and '
        'continue all features that do not need Pump Check.\n\nWithdraw Pump Check permission in '
        'Settings → AI sharing permissions → Pump Check. Confirmed withdrawal blocks new '
        'authorized processing under the old permission. Already-transmitted requests cannot be '
        'recalled. Withdrawal does not delete saved Pump Check photos and results; account '
        'deletion is separate in Settings, and withdrawing the separate health data permission '
        'deletes Pump Check photos and results as described in section 4 of the Privacy Notice. '
        'AWS can keep records to operate its service and detect abuse, which can include '
        'reviewing images that appear to show illegal content. Pump Check gives general fitness '
        'feedback only; it does not identify you and is not medical advice, diagnosis or a '
        'body-composition measurement. AI-generated content may be inaccurate, incomplete, '
        'misleading or out of date. Check important information independently before relying on '
        'it.\n\nFor this operation, information is received by AWS’s Amazon Bedrock service; '
        'content is not sent directly to Anthropic. Processing may take place outside Türkiye '
        'in one of the AWS EU Regions listed in section 5 of the Privacy Notice. See section 5 '
        'for recipients and processing locations, section 9 for the international-transfer '
        'arrangement, section 10 for retention and deletion of records and provider copies, and '
        'section 11 for your rights. You can read this information before deciding: '
        'https://www.axisaiapp.com/privacy. This permission covers only the operation described '
        'above; it does not authorize other AI features or the use of data outside their '
        'permitted scope.\n'
    ),
    ('d1-closure02-pump-check-candidate', 'tr'): (
        "AxisAI'ın Pump Check için seçtiğiniz bir fotoğrafı, seçtiğiniz vücut bölgesi ve "
        'ortamla ve yazdığınız açıklamayla birlikte Amazon Web Services’in (AWS) Amazon Bedrock '
        'hizmetine, Anthropic tarafından geliştirilen Claude modelleriyle işlenmek üzere '
        'göndermesine izin veriyor musunuz? Bir vücut fotoğrafı fiziğinizi gösterir ve dolaylı '
        'olarak sağlıkla ilgili veya diğer hassas bilgileri ortaya koyabilir. Göndermeden önce '
        'sunucumuz fotoğrafı konum meta verisi gibi gömülü ayrıntılar olmadan yeniden kodlar; '
        'bu, fotoğrafta görüneni gizlemez. Geçerli bir sonuç üretmek için gereken yeniden '
        'denemelerde bu bilgiler yetkili bir modele tekrar gönderilebilir.\n\nBu izin yalnızca '
        'gönderdiğiniz tek fotoğrafın analizini kapsar. Fotoğrafları karşılaştırmaz veya zaman '
        'içindeki değişimi ölçmez; Koç konuşmasını, plan oluşturmayı, QR Menü analizini veya '
        "isteğe bağlı AI Hafıza'yı kapsamaz. Fotoğraf seçmek veya Pump Check geçmişinizi "
        'görüntülemek AI izni vermez. Yalnızca kendinize ait ve analiz edilmesini istediğiniz '
        'fotoğrafları gönderin. İzin vermeden Pump Check gerektirmeyen tüm özellikleri '
        'kullanabilirsiniz.\n\nPump Check iznini Ayarlar → Yapay zekâ paylaşım izinleri → Pump '
        'Check yolundan geri çekebilirsiniz. Onaylanan geri çekme, eski izne dayalı yeni '
        'yetkili işlemeyi engeller. Önceden gönderilen istekler geri çağrılamaz. İzni geri '
        "çekmek kayıtlı Pump Check fotoğraflarını ve sonuçlarını silmez; hesap silme Ayarlar'da "
        'ayrı bir işlemdir; ayrı sağlık verisi iznini geri çekmek ise Pump Check fotoğraflarını '
        've sonuçlarını Gizlilik ve Aydınlatma Metni’nin 4. bölümünde açıklandığı şekilde '
        'siler. AWS, hizmetini işletmek ve kötüye kullanımı tespit etmek için kayıt tutabilir; '
        'bu, yasa dışı içerik gösteriyor gibi görünen görsellerin incelenmesini içerebilir. '
        'Pump Check yalnızca genel fitness geri bildirimi verir; sizi tanımlamaz, tıbbi '
        'tavsiye, teşhis veya vücut kompozisyonu ölçümü değildir. Yapay zekâ tarafından '
        'üretilen içerik hatalı, eksik, yanıltıcı veya güncel olmayabilir. Önemli bilgilere '
        'güvenmeden önce bağımsız olarak doğrulayın.\n\nBu işlemde bilgilerin alıcısı AWS’nin '
        'Amazon Bedrock hizmetidir; içerik doğrudan Anthropic’e gönderilmez. İşleme, Türkiye '
        'dışında, Gizlilik ve Aydınlatma Metni’nin 5. bölümünde listelenen AWS AB bölgelerinden '
        'birinde gerçekleşebilir. Alıcılar ve işleme yerleri için 5. bölüme, yurt dışı aktarım '
        'düzenlemesi için 9. bölüme, kayıtların ve sağlayıcı kopyalarının saklanması ve '
        'silinmesi için 10. bölüme, haklarınız için 11. bölüme bakın. Bu bilgileri karar '
        'vermeden önce okuyabilirsiniz: https://www.axisaiapp.com/privacy. Bu izin yalnızca '
        'yukarıda açıklanan işlem içindir; diğer yapay zekâ özelliklerini veya onların '
        'kullanamadığı verileri kullanma izni vermez.\n'
    ),
    ('d1-closure02-qr-menu-candidate', 'en'): (
        'Allow AxisAI to send the content of a public menu web page you scan or enter, and the '
        'food descriptions in it, to Amazon Web Services’ (AWS) Amazon Bedrock service for '
        'processing using Claude models developed by Anthropic, to identify the dishes and '
        'estimate their nutrition values? To read the page, our server first sends a request to '
        'that public https:// website. Your personal targets, totals, body information and '
        'account details are not sent to the AI service; AxisAI uses your targets inside the '
        'app to score the results. Retries needed to produce a valid result may send this '
        'information again to an authorized model.\n\nThis permission covers QR Menu analysis '
        'only, including the nutrition estimates it produces for menu items. It works with '
        'public web and text menu pages; it does not read menu photos or PDF files. It does not '
        'permit Coach conversation, plan generation, Pump Check or optional AI Memory. Scanning '
        'a QR code or viewing earlier menu results does not itself grant AI permission. Items '
        'are added to your food log only if you choose them. You can decline and continue food '
        'search, manual food logging and other non-AI features.\n\nWithdraw QR Menu permission in '
        'Settings → AI sharing permissions → QR Menu. Confirmed withdrawal blocks new '
        'authorized processing under the old permission. Already-transmitted requests cannot be '
        'recalled. Saved menu results, cached menu results and food records you added are not '
        'deleted by withdrawal; account deletion is separate in Settings. Menu estimates are '
        'approximate and can be wrong, including about ingredients and allergens; check with '
        'the restaurant, especially if you have an allergy or a health condition. This is '
        'general nutrition information, not medical or dietetic advice. AI-generated content '
        'may be inaccurate, incomplete, misleading or out of date. Check important information '
        'independently before relying on it.\n\nFor this operation, information is received by '
        'AWS’s Amazon Bedrock service; content is not sent directly to Anthropic. Processing '
        'may take place outside Türkiye in one of the AWS EU Regions listed in section 5 of the '
        'Privacy Notice. See section 5 for recipients and processing locations, section 9 for '
        'the international-transfer arrangement, section 10 for retention and deletion of '
        'records and provider copies, and section 11 for your rights. You can read this '
        'information before deciding: https://www.axisaiapp.com/privacy. This permission covers '
        'only the operation described above; it does not authorize other AI features or the use '
        'of data outside their permitted scope.\n'
    ),
    ('d1-closure02-qr-menu-candidate', 'tr'): (
        "AxisAI'ın taradığınız veya girdiğiniz herkese açık bir menü web sayfasının içeriğini "
        've içindeki yiyecek açıklamalarını, yemekleri tanımak ve besin değerlerini tahmin '
        'etmek için Amazon Web Services’in (AWS) Amazon Bedrock hizmetine, Anthropic tarafından '
        'geliştirilen Claude modelleriyle işlenmek üzere göndermesine izin veriyor musunuz? '
        'Sayfayı okumak için sunucumuz önce o herkese açık https:// web sitesine bir istek '
        'gönderir. Kişisel hedefleriniz, toplamlarınız, vücut bilgileriniz ve hesap '
        'ayrıntılarınız yapay zekâ hizmetine gönderilmez; AxisAI sonuçları puanlamak için '
        'hedeflerinizi uygulama içinde kullanır. Geçerli bir sonuç üretmek için gereken yeniden '
        'denemelerde bu bilgiler yetkili bir modele tekrar gönderilebilir.\n\nBu izin yalnızca QR '
        'Menü analizini ve menü öğeleri için ürettiği besin değeri tahminlerini kapsar. Herkese '
        'açık web ve metin menü sayfalarıyla çalışır; menü fotoğraflarını veya PDF dosyalarını '
        "okumaz. Koç konuşmasını, plan oluşturmayı, Pump Check'i veya isteğe bağlı AI Hafıza'yı "
        'kapsamaz. QR kod taramak veya önceki menü sonuçlarını görüntülemek AI izni vermez. '
        'Öğeler yalnızca siz seçerseniz yemek kaydınıza eklenir. İzin vermeden besin arama, '
        'elle yemek kaydı ve diğer AI dışı özellikleri kullanabilirsiniz.\n\nQR Menü iznini '
        'Ayarlar → Yapay zekâ paylaşım izinleri → QR Menü yolundan geri çekebilirsiniz. '
        'Onaylanan geri çekme, eski izne dayalı yeni yetkili işlemeyi engeller. Önceden '
        'gönderilen istekler geri çağrılamaz. İzni geri çekmek kayıtlı menü sonuçlarını, '
        'önbellekteki menü sonuçlarını ve eklediğiniz yemek kayıtlarını silmez; hesap silme '
        "Ayarlar'da ayrı bir işlemdir. Menü tahminleri yaklaşıktır ve özellikle içerikler ve "
        'alerjenler konusunda hatalı olabilir; özellikle bir alerjiniz veya sağlık sorununuz '
        'varsa restorana danışın. Genel beslenme bilgisidir; tıbbi veya diyetisyen tavsiyesi '
        'değildir. Yapay zekâ tarafından üretilen içerik hatalı, eksik, yanıltıcı veya güncel '
        'olmayabilir. Önemli bilgilere güvenmeden önce bağımsız olarak doğrulayın.\n\nBu işlemde '
        'bilgilerin alıcısı AWS’nin Amazon Bedrock hizmetidir; içerik doğrudan Anthropic’e '
        'gönderilmez. İşleme, Türkiye dışında, Gizlilik ve Aydınlatma Metni’nin 5. bölümünde '
        'listelenen AWS AB bölgelerinden birinde gerçekleşebilir. Alıcılar ve işleme yerleri '
        'için 5. bölüme, yurt dışı aktarım düzenlemesi için 9. bölüme, kayıtların ve sağlayıcı '
        'kopyalarının saklanması ve silinmesi için 10. bölüme, haklarınız için 11. bölüme '
        'bakın. Bu bilgileri karar vermeden önce okuyabilirsiniz: '
        'https://www.axisaiapp.com/privacy. Bu izin yalnızca yukarıda açıklanan işlem içindir; '
        'diğer yapay zekâ özelliklerini veya onların kullanamadığı verileri kullanma izni '
        'vermez.\n'
    ),
})
# Exact active versions, selected only by reviewed source after approval.
# Historical contracts may remain in POLICIES but cannot open new launch work.
LAUNCH_VERSIONS = MappingProxyType({
    "training.plan": "d1-closure01-training-candidate",
    "coach.conversation": "d1-closure02-coach-candidate-r2",
    "nutrition.plan": "d1-closure02-nutrition-plan-candidate",
    "pump_check.analysis": "d1-closure02-pump-check-candidate",
    "qr_menu.analysis": "d1-closure02-qr-menu-candidate",
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
