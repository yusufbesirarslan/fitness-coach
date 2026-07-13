# Cognito CustomEmailSender — markalı auth e-postaları (Resend Sprint 3)

Cognito'nun ürettiği **doğrulama** ve **şifre sıfırlama** kodlarını, Cognito'nun
kendi düz e-postası yerine **markalı AxisAI e-postaları** olarak **Resend**
üzerinden gönderen Lambda + KMS altyapısı.

```
Kullanıcı → Flask (sign_up / forgot_password)
          → Cognito kod üretir, KMS ile şifreler
          → bu Lambda'yı çağırır (CustomEmailSender trigger)
          → Lambda kodu çözer, markalı şablona koyar (email_templates.py)
          → Resend API → AxisAI markalı e-posta
```

Hoş geldin / şifre-değişti e-postaları bu Lambda'dan GEÇMEZ — onları Flask,
`app/services/email_service.py` üzerinden doğrudan gönderir (bkz.
`docs/auth-emails.md`).

## Dosyalar

| Dosya | Ne |
|---|---|
| `template.yaml` | SAM stack: Lambda + KMS anahtarı/alias + Cognito invoke izni |
| `samconfig.toml` | Deploy varsayılanları (eu-central-1). `ResendApiKey` BİLEREK yok |
| `src/handler.py` | Trigger yönlendirme + KMS çözümü; ASLA exception yükseltmez |
| `src/email_sender.py` | urllib Resend göndericisi (özel User-Agent zorunlu — Cloudflare) |
| `src/email_templates.py` | `app/services/email_templates.py`'nin **bayt-bayt kopyası** |
| `src/requirements.txt` | `aws-encryption-sdk` (yalnızca Lambda paketi) |

**Şablon senkronu:** şablonu her zaman `app/services/email_templates.py`'de
değiştir, sonra kopyala — `tests/test_email_templates_sync.py` eşitliği zorlar:

```bash
cp app/services/email_templates.py infra/cognito-email-sender/src/email_templates.py
```

## 1) Stack'i deploy et

```bash
cd infra/cognito-email-sender
sam validate --lint
sam build            # Windows'ta wheel sorunu görürsen: sam build --use-container
sam deploy --parameter-overrides \
    ResendApiKey=<RESEND_API_KEY> \
    AlarmEmail=<alarm-alacak-adres>
```

Çıktılardan `FunctionArn`, `KmsKeyArn` ve `AlarmTopicArn` değerlerini not al.
(API anahtarını asla commit'leme; her deploy'da `--parameter-overrides` ile ver —
parametreyi vermezsen boş kalır ve KOD E-POSTALARI HİÇ GİTMEZ.)

> ### 🔔 `AlarmEmail` ve SNS ONAYI (H5)
> Stack, e-posta gönderimi sessizce ölürse çalan alarmları tanımlar
> (`EmailFailureAlarm`, `EmailLambdaErrorsAlarm`, `EmailLambdaThrottlesAlarm` →
> SNS). **`AlarmEmail` verildikten sonra AWS'in gönderdiği SNS onay e-postasını
> TIKLAMAN gerekir**; onaylanmayan abonelik sessizdir — yani alarm çalar, kimse
> duymaz. Onayı `aws sns list-subscriptions-by-topic --topic-arn <AlarmTopicArn>`
> ile doğrula (`SubscriptionArn` "PendingConfirmation" OLMAMALI).
>
> Neden bu kadar önemli: trigger bağlıyken **Cognito kendi e-postalarını
> göndermez**. Lambda kırılırsa doğrulama/sıfırlama kodları hiç kimseye ulaşmaz
> ama `/register` 200 dönmeye devam eder — kimse kayıt olamaz ve hiçbir gösterge
> kırmızı yanmaz.

## 2) Trigger'ı kullanıcı havuzuna bağla (manuel — havuz IaC'de değil)

Havuz (`eu-central-1_kaX0SORRK`) konsol-yönetimli olduğundan bu adım stack'te
otomatikleştirilemez.

> ### ⚠️ HAVUZUN BEKLENEN YAPILANDIRMASI (H4)
> Havuz IaC'de olmadığı için, uygulamanın kimlik sağlayıcısı hakkındaki
> VARSAYIMLARI sürümlenmemiş konsol ayarlarında yaşar ve sessizce kayabilir.
> `scripts/check_cognito_pool.py` bunları HER DEPLOY'DA doğrular (şimdilik
> bloklamadan; deploy rolüne `cognito-idp:DescribeUserPool` +
> `DescribeUserPoolClient` eklendikten sonra bloklayıcı yapılabilir):
>
> | Ayar | Beklenen | Neden |
> |---|---|---|
> | `PreventUserExistenceErrors` | `ENABLED` | `auth.py` hesap-numaralandırma tradeoff'u **buna dayanır**: `UserNotConfirmedException` yalnızca parola DOĞRUYKEN döndüğü için kabul edilmiştir. Bayrak kapanırsa `/login` ve `/verify` GERÇEK bir numaralandırma oracle'ına döner. |
> | `MfaConfiguration` | `OFF` | `cognito_service.authenticate()` TÜM challenge yanıtlarını reddeder (auth-bypass koruması). MFA açılırsa **hiçbir kullanıcı giriş yapamaz.** |
> | `ExplicitAuthFlows` | `ALLOW_USER_PASSWORD_AUTH` + `ALLOW_REFRESH_TOKEN_AUTH` | native backend akışının kullandığı iki flow. |
> | `Policies.PasswordPolicy.MinimumLength` | ≥ 8 | `validate_password` ile uyumlu olmalı. |
> | `LambdaConfig.CustomEmailSender` | bağlı | yoksa markalı auth e-postaları sessizce Cognito varsayılanlarına döner. |
>
> Elle çalıştırmak için:
> ```bash
> python scripts/check_cognito_pool.py \
>   --pool-id eu-central-1_kaX0SORRK --client-id 3rdtrk3vl1dp0m1d19gdc3pqib
> ```

Önce mevcut konfigürasyonu YEDEKLE (rollback için de gerekir):

```bash
aws cognito-idp describe-user-pool --user-pool-id eu-central-1_kaX0SORRK \
  --region eu-central-1 > pool-before.json
```

> ### ⚠️ FOOTGUN: `update-user-pool` belirtmediğin alanları SIFIRLAR
> `update-user-pool`, çağrıda vermediğin **değiştirilebilir tüm alanları
> varsayılana döndürür** (şifre politikası, auto-verified attributes, e-posta
> yapılandırması, hesap kurtarma, MFA, deletion protection...). Trigger'ı
> eklerken `pool-before.json`'daki mevcut değerleri çağrıya AYNEN taşı.

`pool-before.json`'dan en az şunları taşı: `Policies`,
`AutoVerifiedAttributes`, `EmailConfiguration`, `AccountRecoverySetting`,
`AdminCreateUserConfig`, `UserAttributeUpdateSettings`, `MfaConfiguration`,
`DeletionProtection`, `UserPoolTags` ve `LambdaConfig`'te varsa mevcut diğer
trigger'lar. Sonra `--lambda-config`'e CustomEmailSender'ı ekle:

```bash
# Örnek — <...> değerlerini pool-before.json ve stack çıktılarından doldur.
aws cognito-idp update-user-pool \
  --user-pool-id eu-central-1_kaX0SORRK \
  --region eu-central-1 \
  --policies "$(jq -c .UserPool.Policies pool-before.json)" \
  --auto-verified-attributes email \
  --account-recovery-setting "$(jq -c .UserPool.AccountRecoverySetting pool-before.json)" \
  --admin-create-user-config "$(jq -c '.UserPool.AdminCreateUserConfig | del(.UnusedAccountValidityDays)' pool-before.json)" \
  --deletion-protection "$(jq -r .UserPool.DeletionProtection pool-before.json)" \
  --lambda-config 'CustomEmailSender={LambdaArn=<FunctionArn>,LambdaVersion=V1_0},KMSKeyID=<KmsKeyArn>'
```

Doğrula — SADECE `LambdaConfig` değişmiş olmalı:

```bash
aws cognito-idp describe-user-pool --user-pool-id eu-central-1_kaX0SORRK \
  --region eu-central-1 > pool-after.json
diff <(jq -S .UserPool pool-before.json) <(jq -S .UserPool pool-after.json)
```

**Bu andan itibaren Cognito hiçbir e-postayı kendisi göndermez** — tüm kod
e-postaları Lambda→Resend'den akar. Lambda kırılırsa kullanıcılara kod GİTMEZ
(auth çağrıları yine başarılı döner; handler asla yükseltmez) — bu yüzden
smoke test şart.

## 3) Smoke test (prod)

1. Kullan-at bir kullanıcıyla `/register` → doğrulama kodu e-postası **markalı**
   gelmeli (Resend dashboard'da "sent", CloudWatch'ta `[EMAIL-SENDER]` maskeli
   alıcıyla; düz kod HİÇBİR logda görünmemeli).
2. Kodu `/verify`'da gir → **hoş geldin** e-postası (bu Flask'tan gelir; EC2
   `.env`'inde `RESEND_API_KEY` dolu olmalı).
3. `/forgot-password` → **sıfırlama kodu** e-postası markalı gelmeli.
4. `/reset-password`'da kod + yeni şifre → **şifren değiştirildi** e-postası.
5. Yeni şifreyle giriş yap.

## Rollback

Stack'i silmeye gerek yok — trigger'ı havuzdan ayır, Cognito'nun kendi e-posta
gönderimi ANINDA geri gelir:

```bash
# Yine pool-before.json'daki alanları taşıyarak; --lambda-config'i boş ver:
aws cognito-idp update-user-pool --user-pool-id eu-central-1_kaX0SORRK \
  --region eu-central-1 [...korunan alanlar...] --lambda-config '{}'
```

## Güvenlik notları

- KMS anahtar politikası Cognito principal'ını `aws:SourceArn` (yalnız bu havuz)
  ve `aws:SourceAccount` ile kilitler; Lambda rolü yalnızca bu anahtarda
  `kms:Decrypt` alır; invoke izni havuz ARN'ine kilitlidir.
- Düz kod yalnızca e-posta GÖVDESİNDE yer alır (konu satırları loglanır, kod
  konuya asla yazılmaz); loglar maskeli alıcı + Resend id taşır.
- `ResendApiKey` NoEcho parametredir, log/`describe-stacks` çıktısında görünmez.
- App client'ta `PreventUserExistenceErrors=ENABLED` önerilir (Flask tarafındaki
  jenerik yanıtla birlikte hesap numaralandırmasını iki katmanda kapatır).
