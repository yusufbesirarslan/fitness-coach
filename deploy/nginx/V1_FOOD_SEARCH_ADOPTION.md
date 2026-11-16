# R6 V1 Food Search site adoption

Source preparation only. This PR grants no production execution, installation,
configuration test, reload, deployment, DB operation or provider request.
It does not change the deployment controller, bootstrap or route-switch rules.
The source template is not installed wholesale. The new utility prepares the
single-site candidate for the existing separately authorized adoption path.

## Accepted target evidence

Founder task `AXISAI-R6-NGINX-ADOPTION-IMPLEMENTATION` accepts the existing
`R6_SANITIZED_NGINX_STRUCTURE_V1` catalog (2026-11-16T13:09:24Z) and
`RECOVERY_V2` five-field attestation for `i-0c6f5352fc214e68d`:

| File / SHA-256 | Accepted semantics |
| --- | --- |
| FILE1 `d7be180d12cb332090cf4a0e8de780bd30e7f59fd0ffa4ad42be0e70e6bc6348` | http `types_hash_max_size 2048` at287 |
| FILE3 `f83269eb587148474ef255583794e382ce5104ad11e9528e2184a41a828a9096` | `limit_req_zone $binary_remote_addr zone=perip:10m rate=30r/s` at1875; application `limit_req zone=perip burst=60 nodelay` at5409; TLS IPv6 `ipv6only=on` at6070; HTTP host=NAME1 conditional301 at6498 |

Owner-held source reference:
`outputs/axisai-v1-r6-protected-cutover-execution-readiness-20261116/NGINX_ADOPTION_CLOSURE_20261116/PRODUCTION_TARGET_EVIDENCE/ACCEPTED_SANITIZED_STRUCTURE_20261116.json`.
The subsequent founder five-field receipt supersedes its unresolved-field
disposition. Names remain aliases in published evidence; private bytes are not
exported or reconstructed. The attestation is about disk, not loaded state.

The catalog has exactly two public servers, both in FILE3
(`/etc/nginx/sites-available/fitx`, enabled through a symlink): TLS443 IPv4/IPv6
with NAME1/NAME2, and HTTP80 IPv4/IPv6 with NAME1 and the existing301/404
fallback. Default/unmatched-host traffic uses those same servers. The third
server, FILE4, listens only on127.0.0.1:3000 and is untouched.
Other retained file identities are:

| Role | SHA-256 |
| --- | --- |
| FILE2 included configuration | `b12cd90b120d4b97e76a7b857eb4a2bf0b6df37c4b246d471f37e6493dc191ac` |
| FILE4 loopback proxy | `02caac434a92c33414dbdefd68c9dace90d8b7b4b55055ba33eeaa3d595667bb` |
| FILE5 active upstream (accepted blue) | `7587d991f67b094085b6bfd836129f9086ee89772e76dd47e40cf654071d9a35` |
| FILE6 Certbot options | `5e21cc66989f26ec46116d979421e538131cf8ab33ffff3f682fbfe491b0ace8` |

## Exact delta

`scripts/r6_food_search_adoption.py` reuses the existing token parser, requires
the exact FILE3 SHA, refuses any alternate preimage, and edits only two ranges:

1. Replace the TLS exact Food Search proxy location with404/no-store. No proxy
   handler remains there; application root and upstream stay byte-identical.
2. Before the HTTP host conditional, set an empty cache-header variable and
   add a URI equality guard that sets it to `no-store` and returns404. This
   server-phase refusal runs before the existing301 or404. Empty header
   values are omitted for every other URI, preserving both original behavior
   and headers. Query strings do not affect `$uri` equality. No error-page,
   internal redirect, extra listener, upstream, log rule or server is added.

All names, listeners, TLS/Certbot bytes, rate limits, and application proxy
bytes outside these two ranges survive unchanged. The inactive FatSecret
upstream definition and loopback file are not incidentally removed or probed.

The CLI reads at most64KiB from stdin, emits the complete private candidate to
stdout only after validation, and emits only `R6_ADOPTION_REFUSED` on a
preimage/grammar refusal. It makes no subprocess/network calls and performs
no host-file writes. Keep stdout private; never send it to CI or a terminal.
For the owner's already retained FILE3 bytes, offline preparation is:

```sh
umask 077
python3 -I scripts/r6_food_search_adoption.py \
  < PRIVATE_ACCEPTED_FILE3 > PRIVATE_CANDIDATE_FILE3
# Proceed only on exit0; a refused command can leave an empty redirected file.
sha256sum PRIVATE_ACCEPTED_FILE3 PRIVATE_CANDIDATE_FILE3
```

Never replace the pin with a hash discovered from a changed host. The complete
production postimage SHA becomes available from these actual owner-held bytes;
a structurally equivalent test fixture is not that postimage. Bind it and the
exact script/source revision to the later founder change GO before installing.

## Qualification and loaded acceptance

The existing CI Nginx job runs the added unit tests and real disposable-Nginx
cases: query-free/query-bearing GET/POST, both protocols, IPv4/IPv6, both names,
unknown/default/IP hosts and SNI, blue/green application routing, unchanged
unrelated301/404 and header behavior, zero synthetic provider requests, and
Certbot's unchanged name/listener/TLS signature. These are local fixture
proofs, not production observations or a replacement for prior qualification.

The existing R6 owner's separate adoption acceptance must precede the protected
deployment baseline. Under Yusuf Arslan's founder-approved single-operator
self-custody, bind the actual UTC window, production principal/target, full
catalog continuity and six preimages to the exact reviewed source and actual
candidate postimage. Preserve the canonical production flock and route lock
ordering. Install only FILE3 through its trusted canonical target, retaining
root ownership/mode and the enabled symlink. No whole-template copy, new
permissions, lock repair, Certbot change, route switch or automatic rollback.

The existing release mechanism must bind the accepted candidate to a successful
authorized configuration validation/reload and generation receipt: master
PID/start continuity, every captured old worker PID/start retired, stable new
generation under the existing150s/2s limits, same complete configuration
fingerprint except this reviewed delta, and unchanged serving blue baseline
`94a3f170bff544d68b3e7f656a92ba896b097b37`. Do not rerun the historical
standalone unchanged-configuration barrier. A disk hash or successful `nginx -t`
alone never establishes loaded adoption. No capture is requested by this PR.

After loaded no-proxy proof, use the existing fixed synthetic, no-redirect
response acceptance on all catalog endpoints with no customer data, requiring
404/no-store for GET/POST with/without the fixed query marker. Zero upstream
is supported by the complete loaded no-dispatch rule; do not claim a measured
production count from the fixture counter or response status alone.

Bind final protected release acceptance to the actual separately approved
merged candidate, main CI, successor deployment run/attempt, immutable web and
worker identities and unchanged ingress fingerprint except its canonical
upstream switch. Historical accepted baseline, future application candidate,
this PR head and eventual merge SHA are distinct identities. No earlier run
approval carries forward. Existing failure/stop conditions remain; no automatic
retry, restart, kill or rollback. Missing loaded lineage, postimage, endpoint
coverage or current custody means pending acceptance, never inferred PASS.
