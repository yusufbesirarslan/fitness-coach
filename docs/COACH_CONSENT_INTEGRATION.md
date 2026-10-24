# Coach D1 integration — incomplete, default closed

Backend main base `2e0bdb53a0804e9bcc4c91aa5cc925cfbd5774cf`.
Composed dependency: D1 #464 `c6050c703174432e322f3a824e908eeca59543b4`,
not a claim that #464 has merged to main.
This PR is a prerequisite component, not a functioning Coach integration or
permission to activate. No published disclosure, Coach binding, endpoint
opening, provider call, migration, model-routing or feature-flag change.

## Exact disclosure text

`app/services/ai_consent_disclosures.py` projects only text whose UTF-8 SHA-256
matches the existing authoritative `Disclosure.texts`. Both EN and TR must
exist for the same authoritative policy. The shipped text registry is empty.
The new native component expects `localized_texts: {en: ..., tr: ...}` alongside
the existing metadata in `available_disclosures`. Old digest-only responses
remain denied. This helper still needs the D1 owner's integration into
`ai_consent._public`; it is not yet part of the live GET endpoint.

Grant qualification must ALSO require that the text pair is publishable.
Merely changing status serialization does not close direct API grant bypasses.
The owner must compose that requirement with existing D1 policy validation,
without accepting any runtime or client-defined disclosure content.

## Remaining shared source work

1. D1 operation-fencing owner has supplied the versioned operation interface
   in #464, with real-PG writer tests. Its documented check-then-send HTTP
   residual still does not satisfy acknowledged-withdrawal publication ordering.
   Local checkpoint `6adb17a` is historical, not merged qualification.
2. Integrate one bounded Coach binding into D1 and the shared pipeline after
   reviewing exact context and tool-result categories. Reuse that owner's
   operation token. Do not write a parallel generation authority here.
3. Compose signed cohort authority from eligibility #462 and independently
   qualified first-party health authority. Eligibility's `screen_passed` is
   self-declared cohort evidence; none of its capabilities grants Coach AI.
   No first-party health authorization contract has been supplied.
4. Consent preflight precedes paid allowance reservation and context building.
   Terminal permission exceptions bypass provider failure/retry handling.
   After a possible physical attempt, retain truthful settlement uncertainty.
5. Keep exact model/recipient authorization on every final SDK attempt,
   continuation and fallback. Fence all Coach tool/domain writes, conversation
   persistence and HTTP handoff after provider completion. Do not swallow a
   fence refusal in the pipeline's best-effort `_record` catch.
6. Remove only the qualified blocking Coach endpoint(s) from the early closure;
   streaming, optional Memory and all unrelated AI paths remain closed.

## Native admission composed, endpoint still closed

`mobile_coach.send_coach_message` now captures the owner's `begin_operation`
once using the server's `heavy_primary_route()` before allowance reservation
or context construction. The whole native turn uses `operation_scope`, including
the shared pipeline, tools, persistence and payload projection. This reuses D1's
original generation; no new permission registry or operation authority exists.

Terminal `ConsentDenied` reaching the route returns `consent_error_response`
without adding a provider failure/cooldown. The established provider-door
`attempt_observer` records only whether an attempt might have been transmitted.
A zero-attempt failure can refund the weekly allowance; a possible attempt
cannot. This in-request billing observation is not durable operation recovery.

The provider loops in `ai_coach.py` still need their owner's typed exception
propagation: a swallowed denial cannot be recovered by this route. Coach meal
and workout tool writes, deferred work and the HTTP transport handoff also
need the D1 owner's remaining qualification. Empty policies, absent Coach
binding and every early route closure remain unchanged. Cohort screening does
not replace independent health-processing authority. New route tests use
explicit synthetic layer isolation and qualify composition only, not consent
approval, real-PG ordering or live Coach readiness.

## Internal EN/TR copy draft — NOT APPROVED, NOT PUBLISHABLE

Version: UNASSIGNED. Recipient/model tuple: UNATTESTED. Text and semantic
digests: UNASSIGNED. The recipient paragraph below is an editorial instruction,
not text that may be shown as a reviewed disclosure. Do not put this draft in
either live registry until all exact facts and copy are approved.

### EN

Coach is an AI system. With your permission, AxisAI sends your message and the
fitness context needed for your question to its AI service to generate a reply.
Depending on your question, this may include your fitness preferences, exercise
history, nutrition activity, body measurements, recovery ratings and relevant
injury or health constraints. Please avoid information that is unnecessary for
your question and other people's private information.

[Before approval, insert the exact contracted service recipient(s), model and
inference-profile arrangement, any fallback, and verified processing geography.
Do not infer activation from source defaults or claim vendor retention facts
from generic documentation.]

AI can make mistakes. Coach provides general fitness information and does not
diagnose conditions or replace professional medical advice. Do not rely on it
for urgent symptoms or treatment decisions.

You can decline and keep using independently available first-party features.
You can withdraw Coach AI permission in the app. Once withdrawal is confirmed,
new Coach AI processing and publication of pending protected results are
blocked. Requests already sent to the service cannot be recalled. Withdrawal
does not delete earlier chats or other previously processed data; deletion is
a separate action. This permission does not enable optional AI Memory, body
photo analysis, marketing, or other AI purposes.

Privacy notice: https://www.axisaiapp.com/privacy

### TR

Koç bir yapay zekâ sistemidir. İzin verirseniz AxisAI, yanıt oluşturmak için
mesajınızı ve sorunuzla ilgili gerekli fitness bağlamını yapay zekâ hizmetine
gönderir. Sorunuza göre bu bilgiler fitness tercihleriniz, antrenman geçmişiniz,
beslenme hareketleriniz, vücut ölçüleriniz, toparlanma değerlendirmeleriniz ve
ilgili sakatlık veya sağlık kısıtlarınızı içerebilir. Lütfen sorunuz için
gereksiz bilgileri ve başkalarının özel bilgilerini paylaşmayın.

[Onaydan önce sözleşmedeki gerçek hizmet alıcısını/alıcılarını, model ve
çıkarım profili düzenini, varsa yedek sağlayıcıyı ve doğrulanmış işleme
coğrafyasını ekleyin. Kaynak kodundaki varsayılanlardan etkin yapılandırma
çıkarmayın; genel belgelerden sağlayıcının saklama koşullarını varsaymayın.]

Yapay zekâ hata yapabilir. Koç genel fitness bilgileri sunar; tanı koymaz ve
profesyonel tıbbi danışmanlığın yerini tutmaz. Acil belirtiler veya tedavi
kararları için Koç'a güvenmeyin.

İzin vermeyebilir ve bağımsız olarak sunulan birinci taraf özelliklerini
kullanmaya devam edebilirsiniz. Koç yapay zekâ izninizi uygulamadan geri
çekebilirsiniz. Geri çekme onaylandığında yeni Koç yapay zekâ işlemleri ve
bekleyen korumalı sonuçların yayımlanması engellenir. Hizmete önceden gönderilen
istekler geri alınamaz. İzni geri çekmek önceki sohbetleri veya daha önce
işlenen diğer verileri silmez; silme ayrı bir işlemdir. Bu izin isteğe bağlı
yapay zekâ Hafızasını, vücut fotoğrafı analizini, pazarlamayı veya diğer yapay
zekâ amaçlarını etkinleştirmez.

Gizlilik bildirimi: https://www.axisaiapp.com/privacy

The linked public notice still needs app-specific reconciliation: at inspection
it denied sensitive-data processing and described server geography inconsistent
with verified eu-central-1 staging. A link alone does not qualify the notice.

## Evidence limits

Official AWS references consulted 2026-10-23:
[data protection](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html),
[cross-region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html).
These do not attest AxisAI's runtime recipient, invocation-logging settings,
vendor agreement or retention. Staging-operator identity was verified;
`GetModelInvocationLoggingConfiguration` was explicitly denied by IAM and no
workaround attempted. A sanitized operator attestation is still required.
