# LP15-B1 public menu retrieval

The existing web menu URL contract accepts HTTP and HTTPS. Both are untrusted
remote inputs and pass the same credential, SSRF, redirect, media and resource
policy in `app/services/menu_remote.py`. HTTP is compatibility-preserved
untrusted public retrieval: it provides no transport confidentiality or server
authenticity. No credentials, session cookies, sensitive request body or inbound
headers are attached. Returned content remains untrusted; AI output remains
untrusted application content until the normal menu review flow.

Future native QR/menu intake is **not implemented here**. Its API/input boundary
must validate HTTPS-only before calling this shared HTTP/HTTPS fetcher. Shared
web compatibility does not authorize native HTTP intake. There is no native menu
endpoint, LP15-B2 parser or LP15-C implementation in this change.

## Network authority

`_fetch_page` and `_safe_requests_get` are compatibility facades over one fetch
authority. Main HTML, at most six discovered subpages, WordPress JSON and public
Drive text/HTML all use it. No independent Requests implementation remains in
those callers. Scan cache namespace v2 prevents results acquired through the
previous policy from bypassing admission.

URLs require an explicit HTTP/HTTPS scheme, valid authority, no userinfo,
no control characters/backslashes, no scoped IPv6 address, and port 80/443 or
an implicit default. Schemes are never upgraded or rewritten. All DNS answers
must pass the positive global-address predicate plus private, loopback,
link-local, unspecified, multicast, reserved and special-use exclusions.
IPv4-mapped IPv6 is classified as IPv4. IPv6 transition/translation ranges
(6to4, Teredo, NAT64 well-known/local prefixes), 2001::/23, 3fff::/20,
5f00::/16, 192.0.0.0/24 and 192.88.99.0/24 are conservatively rejected.
Mixed public/private answers reject the whole destination.

The qualified Requests 2.34.2 / urllib3 2.8.0 stack is pinned. An adapter creates a socket to the selected approved **literal IP**, with no
second DNS lookup. Before sending application bytes, it verifies that the peer
is the approved IP and still passes policy. The URL hostname remains the Host,
TLS SNI and certificate authority; certificate verification stays enabled.
There is no process-global DNS monkeypatch. No proxy, automatic redirect,
transport retry or connection reused across origins is permitted.

Each hop uses a fresh Requests Session with `trust_env=False`, no auth and an
empty cookie jar. Environment proxies, `.netrc`, environment CA overrides,
AWS credentials and Cognito tokens cannot configure the session. The worker
inherits only `LANG=C.UTF-8`, closes other descriptors and imports no application
factory or provider. Outbound headers are exactly:

- `User-Agent: AxisAI-Public-Menu/1.0`
- `Accept: text/html,text/plain,text/csv,application/json,application/xhtml+xml`
- `Accept-Encoding: identity`

Caller-supplied headers are ignored. Nonempty caller cookies are rejected.
Authorization, Cookie, Proxy-Authorization and arbitrary credential headers are
absent. GET sends no body. A response cookie never reaches the next hop.

Redirects are manual, with at most five redirects (six requests). HTTP→HTTP,
HTTP→HTTPS and HTTPS→HTTPS are allowed. HTTPS→HTTP is rejected. Every hop repeats
URL, credential, address and connection admission before connection.

## Resource and parsing policy

One scan shares a monotonic 30-second acquisition deadline across initial
fetch, redirects, sequential subpages and WordPress fallback. Each fetch runs in
a disposable subprocess; the parent terminates and reaps it on the remaining
deadline. This bounds blocking DNS, connection/TLS/header reads and slow-drip
bodies, in addition to connect and read timeouts of at most three seconds each.
Waiting for one of four worker slots consumes the same deadline. Parser work
consumes elapsed time before subsequent fetches; parser input, structure and
retained output have separate finite limits. This is not an OS memory ceiling
or parser CPU sandbox.

Each response permits at most 3,000,000 decoded bytes, with an aggregate
8,000,000-byte budget and at most 48 network requests including redirect hops.
Budgets cannot be expanded by legacy timeout/max_bytes arguments. Work is
reserved before a fetch; failed fetches consume their reservation. One extra
byte may be read solely to detect an oversized response, never admitted to the
parser. Thus rejected size probes add at most one byte per fetch beyond the
reserved admission budget. Content-Length is only an early rejection hint;
missing, malformed and falsely small lengths do not bypass streaming admission.

Compression is disabled: identity is requested and any nonidentity
Content-Encoding is rejected **before** decompression or reading the body.
The worker reads raw chunks of at most 8192 bytes without transparent decoding.
Closed MIME allowlist: text/html, application/xhtml+xml, text/plain, text/csv and
application/json. JSON is only consumed as WordPress JSON; ordinary web pages
and Drive do not accept it. UTF-8 replacement decoding is deterministic.
HTML/text cannot dispatch to a binary parser, regardless of filename.

Remote PDFs are unsupported, rejected by MIME or leading PDF magic before any
PDF parser, stream decoder, page enumeration or rasterization. The prior compact
8 MiB expansion and 31-page fixtures now assert **zero parser invocations**.
This intentionally removes Drive PDF success/encrypted/corrupt/OCR compatibility;
PDF is not required for the QR beta. Re-enable only through future LP15-B2
isolated parser work with OS memory/CPU/wall-clock/output/network limits.
No claim is made that pdfminer itself has become bounded.

Remote menu images are disabled, including Drive images. Investigation confirmed
that the previous <=1,500,000-byte path bypassed decode/signature admission, and
larger images did not establish a complete decoder allocation/work bound.
LP15-B1 does not retain that decoder. Pump Check and its image helper are unchanged.

Drive is public untrusted retrieval, with exact Drive/docs host matching and no
exemption. Only bounded text/CSV/HTML exports remain. Confirmation-download
pages are rejected, with no follow-up request or cookie transfer. PDF and image
exports fail closed.

Before BeautifulSoup constructs a DOM, HTML admission caps UTF-8 input at
3,000,000 bytes, tokens/nodes at 6000, depth at 64, attributes per tag at 64,
and start-tag length at 8192. JSON admission has the same byte, depth and 6000
structure/token limits before `json.loads`, including embedded framework/JSON-LD
and WordPress payloads. Retained sections are capped during collection at 100
and 40,000 characters total; category/title strings are at most 256 characters.
Final body is at most 40,000 characters, headings at most 40×256 characters,
framework state at most 15,000 characters (the existing AI boundary uses at most
6000), and discovered links at most ten with six followed. Bounded text traversal
stops at its output cap instead of constructing a large join and then slicing.
Existing AI admission and menu review remain authoritative.

## Failures, logging and evidence

Stable internal categories: MENU_URL_INVALID, MENU_DNS_FAILED,
MENU_DESTINATION_BLOCKED, MENU_PEER_BLOCKED, MENU_HTTPS_DOWNGRADE,
MENU_REDIRECT_LIMIT, MENU_WORK_LIMIT, MENU_DEADLINE, MENU_BODY_LIMIT,
MENU_ENCODING_UNSUPPORTED, MENU_MEDIA_UNSUPPORTED, MENU_PARSE_LIMIT,
MENU_FETCH_FAILED, MENU_COOKIES_FORBIDDEN and
MENU_DRIVE_CONFIRMATION_UNSUPPORTED. They carry no remote exception details.
Existing web status/product semantics are preserved where applicable; disabled
media/Drive compatibility returns a sanitized failure. Logs redact userinfo,
query, fragment and path (paths can contain signed credentials); discovered
URLs and raw network exceptions are not logged.

No new raw HTML, remote file, PDF or header persistence was introduced. Existing
scan cache still stores bounded extracted results under its new namespace.

Offline qualification lives in `tests/test_menu_fetch.py`, with existing menu
and LP15-A regressions. M1–M10 mutate environment inheritance, userinfo, private
destination acceptance, redirect validation, literal-IP pinning, body cap, PDF
dispatch, aggregate budget, HTTP credential generation and HTTPS downgrade.
Mutations run in a disposable source copy and restore modified files byte-exact.
