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

**Dil (LP-14):** `handler._resolve_language` kod e-postasının dilini olaydan
seçer — izinli `clientMetadata.language` → izinli `userAttributes.locale` →
`tr`. Backend SignUp'ta ikisini de, ForgotPassword'de metadata'yı gönderir;
ResendCode'da Cognito metadata iletmediği için kayıtta yazılan `locale`
kullanılır. Ayrıntı: `docs/auth-emails.md` → "Kod e-postalarının dili".

## Dosyalar

| Dosya | Ne |
|---|---|
| `template.yaml` | SAM stack: Lambda + KMS anahtarı/alias + Cognito invoke izni |
| `samconfig.toml` | Deploy varsayılanları (eu-central-1). `ResendApiKey` BİLEREK yok; sarmalayıcı içeriğini birebir doğrular |
| `../../scripts/deploy_email_lambda.py` | KANONİK deploy giriş noktası (bölüm 1) |
| `src/handler.py` | Trigger yönlendirme + KMS çözümü + dil çözücü; ASLA exception yükseltmez |
| `src/email_sender.py` | urllib Resend göndericisi (özel User-Agent zorunlu — Cloudflare) |
| `src/email_templates.py` | `app/services/email_templates.py`'nin **bayt-bayt kopyası** |
| `src/requirements.txt` | `aws-encryption-sdk` (yalnızca Lambda paketi) |

**Şablon senkronu:** şablonu her zaman `app/services/email_templates.py`'de
değiştir, sonra kopyala — `tests/test_email_templates_sync.py` eşitliği zorlar:

```bash
cp app/services/email_templates.py infra/cognito-email-sender/src/email_templates.py
```

## 1) Stack'i deploy et — YALNIZCA korumalı yoldan

Ham `sam deploy` **kanonik yol DEĞİLDİR**: `ResendApiKey` verilmezse parametre
`''`'a düşer ve TÜM kod e-postaları sessizce durur (NoEcho olduğu için changeset
bunu sıradan bir `Modify EmailSenderFunction` olarak gösterir); `AlarmEmail`
verilmezse `HasAlarmEmail` yanlışa döner ve `EmailAlarmSubscription` SİLİNİR.
Kanonik giriş noktası `scripts/deploy_email_lambda.py`'dir:

```bash
# Deploy edilecek revizyonun deposunun kökünden (sarmalayıcı + kaynak commit'li ve temiz):
read -rs RESEND_API_KEY && export RESEND_API_KEY    # ekrana/geçmişe düşmez
python scripts/deploy_email_lambda.py \
    --stack-name axisai-cognito-email-sender --region eu-central-1 \
    --alarm-email <ŞU AN ABONE OLAN alarm adresi> \
    [--profile <prod-yetkili-profil>] \
    [--preserve-parameter UserPoolId=<canlı değer> ...]
unset RESEND_API_KEY
```

Önce `--dry-run` ile aynı komut tüm kontrolleri koşar ve önizlemeyi basar;
SAM'i hiç çağırmaz.

Sarmalayıcı SAM'i başlatmadan önce REDDEDER (çıkış 2) eğer:

- `RESEND_API_KEY` yok / boş / yalnızca boşluk / beklenmeyen biçimde
  (boşluk, tırnak, `<...>` yer tutucu); değer hiçbir mesajda gösterilmez;
- `AlarmEmail` (`--alarm-email` veya `ALARM_EMAIL`) yok / boş / düz bir e-posta
  adresi değil;
- `--stack-name`/`--region` açıkça verilmemiş ya da
  `axisai-cognito-email-sender`/`eu-central-1` değil;
- `samconfig.toml` veya `template.yaml` incelenmiş sözleşmeden kaymış
  (başka stack/bölge, `confirm_changeset` kapalı, eklenmiş
  `parameter_overrides`, `ResendApiKey`'de `NoEcho: true` yok, parametre kümesi
  farklı);
- sarmalayıcının KENDİSİ (`scripts/deploy_email_lambda.py`, çalışan dosya)
  git'te izlenmiyor ya da yerel değişikliği var (düzenlenmiş bir kopya bir
  korumayı kapatmış olabilir);
- `--source-dir` bir git çalışma ağacındaki izlenen
  `infra/cognito-email-sender` değil (ör. gitignore'lu `.aws-sam/` altına
  kopya) ya da o alt ağaçta commit'lenmemiş/izlenmeyen değişiklik var;
- stdin terminal değil (changeset onayını bir insan vermeli).

Sonra sırayla `sam validate --lint` → `sam build --use-container` (Linux
x86_64 `cryptography` wheel'leri için ZORUNLU) → `sam deploy`; ilk hatada DURUR
(çıkış 3), deploy denenmez. Her SAM çağrısına `--region eu-central-1` (ve
varsa `--profile`) açıkça geçer; alt süreç ortamında `AWS_REGION` sabitlenir,
`RESEND_API_KEY`/`SAM_DEBUG` silinir, `SAM_CLI_TELEMETRY=0`.

**Gizli değer:** yalnızca `RESEND_API_KEY` ortam değişkeninden okunur; asla
basılmaz, loglanmaz, dosyaya (samconfig dahil) yazılmaz. SAM parametre değerini
yalnızca argv veya config dosyası üzerinden alır; argv seçildi (diske hiçbir
şey düşmez) — bu yüzden değer `sam deploy` süreci çalıştığı sürece o sürecin
argümanlarında (`ps`) yerel kullanıcılara görünür. Paylaşımlı makinede deploy
etme; `--debug`/`SAM_DEBUG` kullanma (sarmalayıcı ikisini de geçirmez).

**Önceden yakala (salt-okunur, rollback ve doğrulama için gerekli):**

```bash
aws lambda get-function-configuration --function-name <FunctionArn> --region eu-central-1 \
  --query '{CodeSha256:CodeSha256,LastModified:LastModified,MemorySize:MemorySize,Timeout:Timeout}'
aws cloudformation describe-stacks --stack-name axisai-cognito-email-sender \
  --region eu-central-1 --query 'Stacks[0].[Outputs,Parameters]'
aws cognito-idp describe-user-pool --user-pool-id eu-central-1_kaX0SORRK \
  --region eu-central-1 --query 'UserPool.LambdaConfig'
aws sns list-subscriptions-by-topic --topic-arn <AlarmTopicArn> --region eu-central-1
```

> ⚠️ `get-function-configuration`'ı ASLA `--query`'siz çalıştırma: `Environment`
> bloğu `RESEND_API_KEY`'i düz metin basar.

`describe-stacks` `Parameters` çıktısında şablon varsayılanından farklı bir
değer (`UserPoolId`, `AppBaseUrl`, `EmailFrom*`, `EmailReplyTo`) görürsen onu
`--preserve-parameter KEY=VALUE` ile aynen ver; verilmeyen parametre şablon
varsayılanına döner. `--alarm-email` şu an abone olan adresle AYNI olmalı.

**Changeset (insan onayı = asıl güvenlik sınırı).** Sarmalayıcı changeset'i
AWS'ten okuyamaz; SAM onu gösterip `Deploy this changeset?` diye sorar ve
sarmalayıcı hemen öncesinde gizli-değersiz bir kontrol listesi basar. `y`
YALNIZCA changeset tam olarak şuysa:

| İşlem | Kaynak | Tür | Replacement |
|---|---|---|---|
| Modify | `EmailSenderFunction` | `AWS::Lambda::Function` | False |

Aşağıdakilerden HERHANGİ biri görünürse `N`: `EmailKmsKey`, `EmailKmsAlias`,
`CognitoInvokePermission`, `EmailSenderFunctionRole`, `EmailAlarmSubscription`
(özellikle Remove), `EmailAlarmTopic`, `EmailFailureMetricFilter`,
`EmailFailureAlarm`, `EmailLambdaErrorsAlarm`, `EmailLambdaThrottlesAlarm`; ya da
herhangi bir Add/Remove, `Replacement=True`/`Conditional`. Havuzun
`LambdaConfig`'i (trigger bağlantısı) bu stack'te DEĞİLDİR; deploy onu
değiştiremez ve değiştirmemelidir. Changeset `ResendApiKey` değerini
gösteremez — o yalnızca deploy sonrası doğrulanır.

**Deploy sonrası ZORUNLU doğrulama** (hepsi geçmeden deploy doğrulanmış
sayılmaz; sarmalayıcı bu listeyi sonunda basar):

1. `python scripts/check_email_lambda.py --function-name <FunctionArn>` →
   `UYUMLU` (`UYARI` = okunamadı, GEÇMEDİ).
2. `describe-stacks` `Outputs`: `FunctionArn`, `KmsKeyArn`, `AlarmTopicArn`
   önceki yakalamayla aynı.
3. `describe-user-pool --query 'UserPool.LambdaConfig'` önceki yakalamayla
   aynı (`CustomEmailSender.LambdaArn` = `FunctionArn`, `KMSKeyID` = `KmsKeyArn`).
4. `list-subscriptions-by-topic`: alarm aboneliği var ve
   `PendingConfirmation` değil.
5. Bölüm 3'teki smoke test (kod e-postaları; düz kod hiçbir logda yok).

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

Fonksiyonun alias/version'ı YOK (trigger nitelenmemiş ARN'i çağırır); "önceki
sürüme çevirme" diye bir işlem yoktur. **Kod/dil rollback'i = önceki kaynak
revizyonundan AYNI korumalı deploy:**

```bash
git worktree add ../fc-email-rollback <önceki-revizyon>   # mevcut ağaca dokunmaz
read -rs RESEND_API_KEY && export RESEND_API_KEY
# Önceki revizyonda sarmalayıcı varsa oradan, yoksa güncel ağaçtan --source-dir ile:
python scripts/deploy_email_lambda.py \
    --stack-name axisai-cognito-email-sender --region eu-central-1 \
    --alarm-email <aynı adres> [--profile ...] \
    --source-dir ../fc-email-rollback/infra/cognito-email-sender
```

Aynı zorunlu parametreler, aynı changeset kuralı, aynı deploy-sonrası
doğrulama; ek olarak `CodeSha256` önceden yakalanan değere dönmeli.
Sarmalayıcı hiçbir revizyon SEÇMEZ (pull/checkout/reset yapmaz) — verilen
ağacı deploy eder. Sarmalayıcı ve kaynak FARKLI revizyonlardan gelebilir
(güncel sarmalayıcı + önceki worktree); ikisi de ayrı ayrı izlenen+temiz
olmalıdır ve önizleme deploy'dan önce İKİ revizyonu da gösterir
(`Wrapper revision` / `Source revision`).

**Yalnızca ACİL DURUM (dil rollback'i DEĞİL):** Lambda tamamen kırıksa ve
korumalı yeniden deploy mümkün değilse trigger havuzdan ayrılır; Cognito'nun
kendi (markasız) e-postası ANINDA geri gelir:

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
