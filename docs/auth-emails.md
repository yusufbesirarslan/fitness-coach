# Auth E-postaları — Cognito + Resend Mimarisi (Resend Sprint 3)

Cognito kimlik sağlayıcı olarak kalır (hesaplar, şifreler, kodlar, JWT'ler,
güvenlik politikaları); **kullanıcıya giden e-posta deneyimi** Resend'e taşındı.
Uygulama HİÇBİR koşulda kendi doğrulama/sıfırlama kodu üretmez.

## Mimari

```
                          ┌── kod e-postaları (doğrulama + sıfırlama) ──┐
Kullanıcı → Flask ── boto3 ──► Cognito ── kod üretir, KMS ile şifreler
                                  │
                                  ▼
                    CustomEmailSender Lambda (infra/cognito-email-sender)
                       kodu çözer → email_templates → Resend API
                                  │
                                  ▼
                        AxisAI markalı e-posta

                          ┌── olay e-postaları (kodsuz) ──┐
Flask (auth.py) ── app/services/email_service.py (Resend SDK) ──► Resend
   • hoş geldin        (verify_confirm başarısı sonrası)
   • şifren değişti    (reset_password_confirm başarısı sonrası)
```

## E-posta envanteri

| E-posta | Tetikleyen | Gönderen | Şablon |
|---|---|---|---|
| Doğrulama kodu | Cognito `SignUp` / `ResendCode` | Lambda → Resend | `verification_code_email` |
| Sıfırlama kodu | Cognito `ForgotPassword` | Lambda → Resend | `reset_code_email` |
| Hoş geldin | `POST /verify` başarısı | Flask → Resend | `welcome_email` |
| Şifren değiştirildi | `POST /reset-password` başarısı | Flask → Resend | `password_changed_email` |

Şablonların tek kaynağı **`app/services/email_templates.py`** (saf stdlib —
Lambda'da da çalışsın diye). Lambda'daki kopya bayt-bayt aynı olmalı;
`tests/test_email_templates_sync.py` bunu zorlar. Ortak kabuk
(`render_branded_email`) koyu tema + AXISAI wordmark + CTA + footer taşır;
kopya landing sprint'inin (Resend 2) görünümüyle birebirdir.

## Şifre sıfırlama akışı (Sprint 3'te eklendi)

- `GET/POST /forgot-password` — kullanıcı adı alır, `cognito_service.forgot_password`
  çağırır. Yanıt hesap-numaralandırmasına karşı JENERİKTİR: kullanıcı yok /
  doğrulanmamış / bilinmeyen hata → yine "kod gönderildi" (yalnızca Cognito'nun
  throttle'ı dürüst 429 döner). Rate limit: **5 / 15 dk**.
- `GET/POST /reset-password` — kullanıcı adı + kod + yeni şifre;
  `cognito_service.confirm_forgot_password`. Kod/şifre kuralları Cognito'nundur.
  Rate limit: 10 / 15 dk. Başarıda bilgilendirme e-postası best-effort gider.
- Başarılı sıfırlama **TÜM oturumları KAPATIR**: `reset_password`,
  `session_store.delete_for_user(user.id)` ile kullanıcının sunucu tarafındaki
  bütün Cognito oturum satırlarını siler ve Flask-Login durumunu temizler. (Bu,
  Sprint 3'te eklendi; `ConfirmForgotPassword`'ün kendisi Cognito refresh
  token'larını revoke etmez, revoke'u uygulama yapar.)

## Hata sözleşmesi — auth ASLA e-posta yüzünden düşmez

- Flask tarafı: `_send_welcome_email` / `_send_password_changed_email` tüm
  gövdeyi try/except'e alır (şablon + DB + gönderim); hata `[AUTH-EMAIL]`
  uyarısıyla loglanır, route yanıtı DEĞİŞMEZ. `email_service` zaten varsayılan
  graceful'dır (loglar, None döner).
- Lambda tarafı: `handler.handler` her hatayı yutar ve olayı geri döndürür —
  aksi halde Cognito SignUp/ForgotPassword çağrısı kullanıcıya hata dönerdi.

### Bedeli ve ALARM (H5)

Yutma sözleşmesi doğrudur ama bedeli ağırdır: trigger havuza bağlı olduğu için
**Cognito artık kendi e-postalarını GÖNDERMEZ**. Lambda kırılırsa (bozuk
`RESEND_API_KEY`, Resend kesintisi, KMS izin kayması, `aws-encryption-sdk` sürüm
atlaması) doğrulama ve şifre-sıfırlama kodları **hiçbir kullanıcıya ulaşmaz**,
ama `/register` ve `/forgot-password` neşe içinde 200 dönmeye devam eder. Yani
kimse kayıt olamaz, kimse şifresini sıfırlayamaz ve hiçbir gösterge kırmızı
yanmaz. Bu, sistemdeki en yüksek sonuçlu / en düşük görünürlüklü arıza modudur.

Bu yüzden `infra/cognito-email-sender/template.yaml` şunları tanımlar:

- **`EmailFailureMetricFilter`** — Lambda log grubunda `[ERROR]` / `[WARNING]` /
  `RESEND_API_KEY` desenlerini sayar (`AxisAI/CognitoEmail EmailSendFailures`).
  Sonuncusu "anahtar yok → sessiz no-op" vakasını yakalar.
- **`EmailFailureAlarm`** — 5 dk içinde ≥1 hata → SNS.
- **`EmailLambdaErrorsAlarm` / `EmailLambdaThrottlesAlarm`** — handler istisnaları
  yuttuğu için Lambda `Errors` metriğine yalnızca handler'a ULAŞAMAYAN arızalar
  düşer (INIT timeout / OOM — 256MB+5s ile bir kez yaşandı).

**DLQ neden yok:** handler sözleşme gereği asla yükseltmez, dolayısıyla Lambda
"başarılı" görünür ve DLQ'ya hiçbir şey düşmez. Yutulan istisna asıl arıza
modudur; doğru enstrüman bu yüzden log metric filter'dır.

Alarm e-postası `sam deploy --parameter-overrides AlarmEmail=<adres>` ile bağlanır
ve **SNS onay e-postası tıklanmalıdır**, yoksa abonelik aktifleşmez.

## Loglama / PII politikası

- Loglanır: e-posta türü + trigger, MASKELİ alıcı (`y***@example.com`,
  `email_service.mask_email`), Resend mesaj id'si.
- ASLA loglanmaz: doğrulama/sıfırlama kodları (kodlar yalnızca e-posta
  GÖVDESİNDE — konu satırları loglandığı için konuya da yazılmaz), API
  anahtarları (`_sanitize`), token'lar, şifreler, ham alıcı adresi.

## Ortam değişkenleri

| Değişken | Nerede | Ne |
|---|---|---|
| `RESEND_API_KEY` | Flask `.env` | hoş geldin / şifren-değişti e-postaları (boş → no-op) |
| `EMAIL_FROM_NAME/ADDRESS`, `EMAIL_REPLY_TO` | Flask `.env` | From/Reply-To (varsayılanlar config.py'de) |
| `APP_BASE_URL` | Flask `.env` (opsiyonel) | e-postalardaki CTA bağlantı tabanı |
| `ResendApiKey`, `AppBaseUrl`, `EmailFrom*` | SAM parametreleri | Lambda'nın kendi kopyaları (`infra/cognito-email-sender/template.yaml`) |

## Deploy gereksinimleri

1. Flask tarafı mevcut yolla gider: `main`'e push → `deploy.yml` (EC2). EC2
   `.env`'inde `RESEND_API_KEY` dolu olmalı.
2. Lambda + KMS: `infra/cognito-email-sender/` altında `sam build && sam deploy`.
3. Trigger'ı havuza bağlama **manueldir** (havuz IaC'de değil):
   `infra/cognito-email-sender/README.md` runbook'unu izle — özellikle
   `update-user-pool`'un belirtilmeyen alanları SIFIRLADIĞI uyarısını.

Sıralama önemli: routes/şablonlar trigger'dan ÖNCE canlıya çıkabilir (Cognito
o ana dek kendi düz e-postasını göndermeye devam eder); trigger bağlandığı an
tüm kod e-postaları markalı olur.

## Güvenlik değerlendirmesi

- Cognito güvenlik garantileri korunur: kodları/politikaları Cognito üretir ve
  doğrular; uygulama ve Lambda kodu yalnızca İLETİR.
- Hesap numaralandırması: `/forgot-password` jenerik yanıt + (önerilen) app
  client'ta `PreventUserExistenceErrors=ENABLED`.
- KMS anahtarı: Cognito principal'ı `aws:SourceArn`/`aws:SourceAccount` ile bu
  havuza kilitli; Lambda rolü yalnızca `kms:Decrypt`; invoke izni havuz ARN'ine
  kilitli. `ResendApiKey` NoEcho.
- Rate limit: 3/15dk (forgot) + 10/15dk (reset) + Cognito'nun kendi
  `LimitExceededException` throttle'ı.
- Bilinen artık risk (kapsam dışı, bilinçli): sıfırlama mevcut oturumları
  düşürmez; Lambda kırığında kod e-postaları sessiz kesilir (smoke test +
  CloudWatch ile izlenir).

## Prod deploy kontrol listesi

- [ ] `main` merge + EC2 deploy yeşil (`gh run watch`)
- [ ] EC2 `.env`: `RESEND_API_KEY` dolu (hoş geldin/şifren-değişti için)
- [ ] `sam deploy` — `axisai-cognito-email-sender` stack'i, gerçek `ResendApiKey` ile
- [ ] Runbook ile `update-user-pool` (describe → alanları koru → trigger ekle → diff doğrula)
- [ ] App client'ta `PreventUserExistenceErrors=ENABLED` kontrolü
- [ ] Smoke: kayıt → markalı kod maili → verify → hoş geldin → forgot →
      sıfırlama kodu → reset → şifren-değişti → yeni şifreyle giriş
- [ ] Resend dashboard + CloudWatch: maskeli loglar, hiçbir logda düz kod yok
- [ ] `pool-before.json` saklandı (rollback: `--lambda-config '{}'`)

## Test haritası

| Dosya | Kapsam |
|---|---|
| `tests/test_email_templates.py` | 4 şablonun sözleşmesi, marka, kod-konuda-yok, escape |
| `tests/test_email_templates_sync.py` | Flask ↔ Lambda şablon kopyası bayt eşitliği |
| `tests/test_email_service.py` | Resend altyapısı + `mask_email` + maskeli loglar |
| `tests/test_cognito_idp.py` | `forgot_password` / `confirm_forgot_password` sarmalayıcıları |
| `tests/test_password_reset.py` | route'lar: jenerik yanıt, throttle, bildirim maili, e-posta hatası bloklamaz |
| `tests/test_auth.py` | hoş geldin maili: başarıda gider, hatada verify'ı düşürmez |
| `tests/test_cognito_email_sender.py` | Lambda: trigger→şablon, asla-yükseltme, log hijyeni |

Hepsi hermetiktir: AWS/ağ yok, `aws-encryption-sdk` import edilmez
(`handler._decrypt_code` lazy + monkeypatch), Resend HTTP'si sahtelenir.
