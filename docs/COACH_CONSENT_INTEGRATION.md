# Coach D1 integration — engineering only, default closed

Existing PR #463 composes merged #464 operation authority. Founder task
PRIV-D1-BACKEND-463-OWNERSHIP-AND-P1-RESOLUTION-06 reassigns only the remaining
integration hunks to this integration owner. Other owner worktrees are retained.
No new policy authority, approved legal copy, provider facts or migration exists.

## Disclosure and grant

`ai_consent._public` projects exact EN/TR UTF-8 text only when both documents
match the source-owned `Disclosure.texts` digests. `mutate` independently checks
that same complete pair inside its existing owner transaction before accepting
an affirmative grant. Missing, partial or altered publication cannot be granted
by bypassing the status endpoint. The shipping policy/text registries remain
empty and Coach has no binding. Historical records retain their contract digest.

## Original operation and canonical writes

The native route captures one `OperationAuthority` before quota/context and
retains it through provider attempts, tools, conversation writes and response
projection. Provider loops rethrow `ConsentDenied` before broad/superclass catches;
no denial becomes a fallback reply, retry, cooldown or replacement provider call.
Existing possible-attempt accounting remains authoritative for quota refunds.

Meal/workout staging locks the owner, binds original server-held authority into
the pending payload and commits under that lock. Confirmation fences that original
authority, even in a newer turn, before claiming or writing a canonical log.
Revoke→regrant cannot revive an earlier operation. Authority metadata is removed
from display/provider result payloads. Existing plan continuation authority from
#464 is preserved. Conversation creation and turn writes fence the active original
operation; typed fence denials propagate out of best-effort memory handling.

## Actual HTTP publication boundary

`app/consent_publication.py` protects native Coach success and operation-derived
fallback status responses. The route stamps the immutable server authority while
the authenticated subject is live. After Flask teardown, middleware buffers at
most 1 MiB and accepts only the exact Gunicorn 26 HTTP/1 Response/plain socket
adapter. Unknown versions, TLS-at-Gunicorn, HTTP/2 and other adapters fail closed.
Production's configured threads=8 selects Gunicorn gthread; no worker, timeout,
nginx or deployment setting is changed by this integration.

The existing D1 owner row lock spans final generation validation AND actual
header/body socket sends. A synchronous nonblocking sender uses one monotonic
absolute 750 ms budget across all partial sends and backpressure waits, below
the existing 2 s mutation lock timeout. There is no background writer. On timeout,
disconnect, denial or interruption, socket shutdown and close precede release of
the serialization lock. Partial responses never receive a fallback body, retry or
chunk terminator. Success has fixed Content-Length, forces connection close, and
returns no bytes for Gunicorn to transmit later. WSGI start_response, serialization
and authorization checks alone are not socket completion.

The boundary is backend socket acceptance, commonly by nginx. Bytes already
accepted by kernel/proxy can arrive at a device after withdrawal acknowledgement
and cannot be recalled. A local real nginx fixture tests HTTP/1.1 upstream with
proxy_buffering off; it does not attest a currently deployed runtime. nginx may
emit its own 502 after a denied upstream closes; no protected body is supplied.

## Qualification and holds

`test_coach_consent_boundaries_pg.py` exercises synthetic exact grants, original
meal/workout confirmation authority, account switches, conversation denial and
typed first/later provider-loop attempts. `test_coach_publication_transport_pg.py`
runs real PostgreSQL, a Gunicorn gthread process and native route→pipeline→
conversation→projection→socket path with synthetic identity/provider facts only.
Events and publisher-specific PostgreSQL lock waits order both race directions;
TCP sends are not mocked. It covers zero readers, slow readers, partial writes,
timeouts, disconnect, SIGKILL, revoke→regrant and real nginx. The macOS fixture
explicitly disables local libpq GSS negotiation for fork safety; no production
connection options are changed. CI runs both suites in its PostgreSQL job and
qualifies the existing branch's exact HEAD separately from PR merge refs.

Final acceptance requires exact-head hosted CI and independent security review;
local test counts and reports live in the founder task's evidence directory.
All endpoint closures, absent Coach binding, empty policy/text registries,
streaming/Memory and activation holds remain. Eligibility screening does not
replace independent health-processing authority. No live provider calls, merge,
deployment, mobile implementation or production changes are authorized here.

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
