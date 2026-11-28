"""FatSecret egress policy (founder disposition #547 c7240107959, Option A).

Every value here is synthetic. The phone, TCKN-shaped, IBAN and card numbers
are well-known fixture shapes, not real people's data.

The provider transport is a fake session: a withheld query or rejected
identifier must produce ZERO token POSTs and ZERO GETs (so no retry, method
fallback or concurrency slot either); an allowed one must produce exactly the
pre-policy request with byte-identical parameters.
"""
import contextlib
import logging
import time
import uuid
from enum import IntEnum
from types import SimpleNamespace

import pytest

from app.models import MealLog
from app.services import fatsecret, mobile_auth, mobile_food_discovery, provider_retention
from app.services import fatsecret_egress_policy as policy

PREFIX = "/api/v1/nutrition"

# --- the accepted assessment matrix (37 withheld / 72 allowed / 22 IDs) --------------------------

BLOCK = [
    ("ahmet.yilmaz@gmail.com", "email_or_handle"), ("ahmet@", "email_or_handle"),
    ("ayse＠hotmail.com", "email_or_handle"), ("@ayse_fit", "email_or_handle"),
    ("https://example.com/x", "url"), ("www.example.org", "url"), ("benimsitem.com.tr", "url"),
    ("0532 123 45 67", "no_letter"), ("+90 532 123 4567", "no_letter"),
    ("tel 0532 123 45 67", "long_number"), ("ara 05321234567", "long_number"),
    ("12345678950", "no_letter"), ("tc 12345678950", "long_number"),
    ("TR33 0006 1005 1978 6457 8413 26", "long_number"),
    ("kart 4111 1111 1111 1111", "long_number"),
    ("ahmet 0532​123​4567", "long_number"),
    ("ali ０５３２１２３４５６７", "long_number"),
    ("ali ٠٥٣٢١٢٣٤٥٦٧", "long_number"),
    ("telefon numaram", "contact_keyword"), ("TELEFON", "contact_keyword"),
    ("whatsapp ahmet", "contact_keyword"), ("IBAN", "contact_keyword"),
    ("cep no ayse", "contact_keyword"), ("ev adresim kadikoy", "contact_keyword"),
    ("my doctor said no sugar", "first_person_health"),
    ("I have diabetes what can I eat", "first_person_health"),
    ("şeker hastalığım var ben ne yiyeyim", "first_person_health"),
    ("BENİM DOKTORUM yağsız dedi", "first_person_health"),
    ("i'm pregnant snacks", "first_person_health"),
    ("insulin dozum için bana ekmek", "first_person_health"),
    ("kemoterapi sonrasi annem icin corba", "first_person_health"),
    ("Food.com recipe", "url"), ("ahmet nokta yilmaz at gmail", "contact_keyword"),
    ("100", "no_letter"), ("12", "no_letter"), ("1/2", "no_letter"), ("ab\ncd", "control"),
]
ALLOW = [
    "chicken breast", "tavuk göğsü", "2% milk", "1% low fat milk", "7up", "7 Up", "5 hour energy",
    "85/15 ground beef", "93% lean ground turkey", "100 calorie pack", "Ben & Jerry's",
    "Uncle Ben's rice", "Me & the Bees lemonade", "My/Mo mochi", "Dr. Pepper", "Dr Pepper Zero",
    "M&M's", "A&W root beer", "Häagen-Dazs", "Müller yogurt", "Ülker çikolatalı gofret",
    "İçim süt", "ISTANBUL simit", "Kellogg's Frosted Mini-Wheats Original Bite Size",
    "Ben & Jerry's Chocolate Chip Cookie Dough", "gluten free bread", "lactose free milk",
    "keto bread", "diabetic friendly chocolate", "sugar free", "allergy friendly cookies",
    "low sodium soup", "pregnancy tea", "Atkins bar", "Smart Balance", "Quest protein bar 60g",
    "Big Mac", "Whopper Jr", "Chick-fil-A nuggets 8 count", "Subway 6 inch turkey", "Lay's 1 oz",
    "Coke 12 fl oz", "Pepsi 2 liter", "yoğurt 250 g", "ayran 300 ml", "mercimek çorbası",
    "menemen", "lahmacun", "köfte", "baklava 2 dilim", "türk kahvesi", "çay şekersiz", "pide",
    "simit", "dürüm", "ch", "mi", "sa", "Sara Lee", "Marie Callender's", "Little Debbie",
    "Papa John's", "Annie's mac", "Chef Boyardee", "1000 island dressing", "V8 juice",
    "3 Musketeers", "Act II popcorn", "Mountain Dew Code Red", "Mr. Kipling", "Tanita", "mi goreng",
]
# Documented residual: passes by design or limitation (RESIDUAL_RISK_AND_LEGAL_HANDOFF.md).
RESIDUAL = ["Ahmet Yılmaz", "ayse yilmaz kadikoy", "ahmet.yilmaz", "beş üç iki bir iki üç",
            "diabetes", "şeker hastası", "metformin", "I hate my boss", "chemo diet",
            "her doctor said rice"]
IDS = [
    ("35755", "35755"), (35755, "35755"), ("1", "1"),
    ("9223372036854775807", "9223372036854775807"), ("9223372036854775808", None),
    ("0", None), ("00123", None), (" 35755", None), ("35755 ", None), ("35755\n", None),
    ("+35755", None), ("-1", None), ("3.5", None), ("٣٥٧", None), ("３５７", None), ("¹²", None),
    ("abc", None), ("my email a@b.co", None), (True, None), (None, None), ("", None), ("1" * 20, None),
]


def test_matrix_sizes_match_the_accepted_assessment():
    assert (len(BLOCK), len(ALLOW), len(IDS)) == (37, 72, 22)


@pytest.mark.parametrize("query, rule", BLOCK)
def test_withheld_queries_name_their_rule(query, rule):
    assert policy.withheld_search_rule(query) == rule


@pytest.mark.parametrize("query", ALLOW)
def test_food_and_brand_queries_pass(query):
    assert policy.withheld_search_rule(query) is None


@pytest.mark.parametrize("query", RESIDUAL)
def test_documented_residual_still_passes(query):
    # Pinned so the legal handoff stays true: deterministic screening is not
    # anonymisation. If one of these starts being withheld, update the docs.
    assert policy.withheld_search_rule(query) is None


@pytest.mark.parametrize("query, rule", [
    ("\x00chicken", "control"), ("chicken\x7f", "control"), ("tab\tfood", "control"),
    ("", "no_letter"), ("   ", "no_letter"), ("!!!", "no_letter"), ("½", "no_letter"),
    ("​​", "no_letter"),                       # Cf only
    ("ayse﹫mail", "email_or_handle"),                    # small commercial at → @
    ("HTTP://X.ORG", "url"), ("ftp://files", "url"), ("site.io/menu", "url"),
    ("ahmet 0532-123-45-67", "long_number"), ("ahmet (0532) 123 4567", "long_number"),
    ("x 1.234.567", "long_number"), ("ali ０５３２­１２３４５６７", "long_number"),
    ("TELEFON NUMARAM", "contact_keyword"), ("E-POSTA", "contact_keyword"),
    ("Instagram ayse", "contact_keyword"), ("tc kimlik", "contact_keyword"),
    ("MY DOCTOR", "first_person_health"), ("benim ilacım", "first_person_health"),
    ("İLAÇ BENİM", "first_person_health"), ("I’m pregnant", "first_person_health"),
    ("me: blood sugar", "first_person_health"),
    (None, "control"), (b"chicken", "control"), (35755, "control"),
])
def test_normalisation_edges(query, rule):
    assert policy.withheld_search_rule(query) == rule


@pytest.mark.parametrize("query", [
    "chi​cken",                       # zero-width inside a food: detection only, still a food
    "ＣＨＩＣＫＥＮ breast", "Çiğ köfte", "IĞDIR kayısısı", "100 g pirinç", "2 x 100 g rice",
    "6 inch sub", "12 fl oz 355 ml", "1,5 lt ayran", "%0 yağ süt", "Coca-Cola 330ml",
    "1 1/2 cup oats", "ice cream", "my/mo", "ben", "medicine ball",
])
def test_more_legitimate_food_input_passes(query):
    assert policy.withheld_search_rule(query) is None


@pytest.mark.parametrize("prefix, rule", [
    # Automatic search sends any 350 ms pause; the fragments after a trigger are withheld.
    ("ah", None), ("ahmet", None), ("ahmet.yil", None),          # residual: before the trigger
    ("ahmet.yil@", "email_or_handle"), ("ahmet.yil@gm", "email_or_handle"),
    ("0", "no_letter"), ("05", "no_letter"), ("0532 12", "no_letter"), ("0532 123 45", "no_letter"),
    ("tel 0532 12", "contact_keyword"), ("ara 053212", None), ("ara 0532123", "long_number"),
    ("www", None), ("www.", "url"), ("my doc", None), ("my doctor", "first_person_health"),
])
def test_partial_automatic_search_input(prefix, rule):
    assert policy.withheld_search_rule(prefix) == rule


def test_screening_never_rewrites_or_returns_query_text():
    for query in [q for q, _ in BLOCK] + ALLOW:
        rule = policy.withheld_search_rule(query)
        assert rule is None or rule in policy.RULES


def test_policy_cost_is_bounded():
    worst = ("ahmet 0532 my doctor kemoterapi tel " * 3)[:100]
    started = time.perf_counter()
    for _ in range(2000):
        policy.withheld_search_rule(worst)
        policy.withheld_search_rule("Kellogg's Frosted Mini-Wheats Original Bite Size")
    per_call = (time.perf_counter() - started) / 4000
    assert per_call < 0.002, per_call   # assessment reports ~18 µs; generous CI bound


def test_huge_int_is_range_checked_before_str():
    # str() of an int this large raises ValueError (int max str digits).
    assert policy.canonical_food_id(10 ** 5000) is None
    assert policy.canonical_food_id(-(10 ** 5000)) is None


class _Big(IntEnum):
    ID = 35755


@pytest.mark.parametrize("value, expected", IDS + [
    (1, "1"), (2 ** 63 - 1, "9223372036854775807"), (2 ** 63, None), (0, None), (-35755, None),
    (_Big.ID, "35755"), (False, None), (35755.0, None), ("35755.0", None), ("1e5", None),
    ("0x10", None), ("35_755", None), ("35755\x00", None), ("​35755", None), ("３", None),
    ("٣", None), ("१२३", None), ("Ⅻ", None), (b"35755", None), (["35755"], None), ({"id": 1}, None),
    ("0" * 19 + "1", None), ("9" * 19, None), ("1" + "0" * 18, "1" + "0" * 18),
])
def test_canonical_food_id(value, expected):
    result = policy.canonical_food_id(value)
    assert result == expected
    assert result is None or type(result) is str


# --- egress: a fake provider transport records every token POST and GET -------------------------

class _Response:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def _food(food_id):
    return {"food": {"food_id": food_id, "food_name": "Chicken Breast", "brand_name": "",
                     "servings": {"serving": [{
                         "serving_id": "50321", "serving_description": "100 g",
                         "metric_serving_amount": "100.000", "metric_serving_unit": "g",
                         "calories": "165", "protein": "31", "carbohydrate": "0", "fat": "3.6"}]}}}


class FakeSession:
    def __init__(self):
        self.gets, self.posts = [], []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _Response({"access_token": "synthetic-token", "expires_in": 86400})

    def get(self, url, **kwargs):
        params = dict(kwargs.get("params") or {})
        self.gets.append((url, params))
        if params.get("method") == "foods.search":
            return _Response({"foods": {"food": [
                {"food_id": "35755", "food_name": "Chicken Breast", "brand_name": ""}]}})
        if params.get("method") == "food.find_id_for_barcode":
            return _Response({"food_id": {"value": self.barcode_food_id}})
        return _Response(_food(str(params.get("food_id"))))

    barcode_food_id = "35755"


@pytest.fixture
def transport(monkeypatch, food_search_reopened):
    session = FakeSession()
    monkeypatch.setattr(fatsecret, "_get_fs_session", lambda: session)
    monkeypatch.setitem(fatsecret._fs_token_cache, "token", None)
    monkeypatch.setitem(fatsecret._fs_token_cache, "expires_at", 0)
    monkeypatch.setenv("FATSECRET_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("FATSECRET_CLIENT_SECRET", "synthetic-secret")
    return session


@pytest.fixture
def slots(monkeypatch):
    taken = []

    @contextlib.contextmanager
    def slot():
        taken.append(1)
        yield

    monkeypatch.setattr(mobile_food_discovery, "blocking_concurrency_slot", slot)
    return taken


def _no_egress(session):
    assert session.gets == [] and session.posts == []


@pytest.mark.parametrize("query", [q for q, _ in BLOCK])
@pytest.mark.parametrize("strict", [True, False])
def test_withheld_search_makes_no_token_or_provider_request(app, transport, query, strict, caplog):
    caplog.set_level(logging.DEBUG)
    with app.app_context():
        assert fatsecret._food_search_raw(query, strict=strict) == ([] if strict else None)
    _no_egress(transport)
    assert query not in caplog.text


@pytest.mark.parametrize("query", ALLOW)
def test_allowed_search_sends_byte_identical_parameters(app, transport, query):
    with app.app_context():
        foods = fatsecret._food_search_raw(query, strict=True)
    assert [f["food_id"] for f in foods] == ["35755"]
    assert len(transport.posts) == 1
    assert transport.gets == [(fatsecret.FATSECRET_API_URL, {
        "method": "foods.search", "search_expression": query, "format": "json", "max_results": 8})]


@pytest.mark.parametrize("value, _expected", [i for i in IDS if i[1] is None])
@pytest.mark.parametrize("strict", [True, False])
def test_invalid_food_id_never_reaches_the_token_or_any_method(app, transport, value, _expected, strict):
    with app.app_context():
        assert fatsecret._food_get_raw(value, strict=strict) is None
        assert fatsecret._food_get_servings(value) is None    # legacy web path
    _no_egress(transport)


@pytest.mark.parametrize("value, canonical", [(35755, "35755"), ("35755", "35755"),
                                              ("9223372036854775807", "9223372036854775807")])
def test_valid_food_id_request_is_unchanged(app, transport, value, canonical):
    with app.app_context():
        food = fatsecret._food_get_raw(value, strict=True)
    assert food["food_id"] == canonical
    assert len(transport.posts) == 1
    assert transport.gets == [(fatsecret.FATSECRET_API_URL, {
        "method": "food.get.v4", "food_id": canonical, "format": "json"})]


def test_valid_id_keeps_the_v4_v2_v1_fallback(app, transport, monkeypatch):
    answers = iter([{"error": {"code": "106"}}, {"error": {"code": "106"}}, _food("35755")])
    monkeypatch.setattr(transport, "get", lambda url, **kw: (
        transport.gets.append(dict(kw["params"])) or _Response(next(answers))))
    with app.app_context():
        assert fatsecret._food_get_raw("35755", strict=True)["food_id"] == "35755"
    assert [g["method"] for g in transport.gets] == ["food.get.v4", "food.get.v2", "food.get"]


# --- transport backstop: any other present or future caller --------------------------------------

@pytest.mark.parametrize("params", [
    {"method": "food.get.v4", "food_id": "abc", "format": "json"},
    {"method": "food.get", "food_id": "0"},
    {"method": "food.get", "food_id": None},
    {"method": "food.get", "food_id": ["35755", "1"]},
    [("method", "food.get"), ("food_id", "00123")],
    {"method": "foods.search", "search_expression": "ahmet@gmail.com"},
    {"method": "foods.search", "search_expression": "my doctor said"},
    {"method": "foods.search", "search_expression": None},
    "method=food.get&food_id=35755",            # an unscreenable raw query string
    b"food_id=1",
    [("food_id",)],
    42,
])
def test_backstop_refuses_before_the_session(app, transport, params):
    with app.app_context():
        with pytest.raises(fatsecret.ProviderRequestWithheld):
            fatsecret._fs_get(fatsecret.FATSECRET_API_URL, params=params)
    _no_egress(transport)
    assert issubclass(fatsecret.ProviderRequestWithheld, fatsecret.FoodProviderUnavailable)


@pytest.mark.parametrize("url", [fatsecret.FATSECRET_API_URL + "?food_id=abc",
                                 fatsecret.FATSECRET_API_URL + "?search_expression=a%40b"])
def test_backstop_also_reads_a_query_in_the_url(app, transport, url):
    with app.app_context():
        with pytest.raises(fatsecret.ProviderRequestWithheld):
            fatsecret._fs_get(url, params={"method": "food.get"})
    _no_egress(transport)


@pytest.mark.parametrize("params", [
    {"method": "food.get.v4", "food_id": "35755", "format": "json"},
    {"method": "food.get.v4", "food_id": 35755, "format": "json"},
    {"method": "foods.search", "search_expression": "tavuk göğsü", "max_results": 5},
    {"method": "food.find_id_for_barcode", "barcode": "0012345678905"},
    None,
])
def test_backstop_passes_canonical_requests_unchanged(app, transport, params):
    with app.app_context():
        fatsecret._fs_get(fatsecret.FATSECRET_API_URL, params=params)
    assert transport.gets == [(fatsecret.FATSECRET_API_URL, dict(params or {}))]


def test_switch_off_still_refuses_before_the_backstop(app, monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(fatsecret, "_get_fs_session", lambda: session)
    from app.services import launch_food_search
    with app.app_context():
        with pytest.raises(launch_food_search.FoodSearchExcluded):
            fatsecret._fs_get(fatsecret.FATSECRET_API_URL, params={"food_id": "abc"})
    _no_egress(session)


def test_legacy_menu_search_helper_is_backstopped(app, transport):
    with app.app_context():
        assert fatsecret._fs_search_foods("ahmet@gmail.com", "synthetic-token") == []
        assert fatsecret._fs_search_foods("mercimek çorbası", "synthetic-token") != []
    assert [g[1]["search_expression"] for g in transport.gets] == ["mercimek çorbası"]


def test_legacy_barcode_name_lookup_never_sends_a_non_canonical_provider_id(app, transport):
    transport.barcode_food_id = "x-1"
    with app.app_context():
        result = fatsecret._food_find_by_barcode("0012345678905")
    assert result["servings"] == []
    assert [g[1]["method"] for g in transport.gets] == ["food.find_id_for_barcode"]


def test_legacy_coach_search_falls_through_without_provider_egress(app, transport):
    with app.app_context():
        assert fatsecret._food_search_fatsecret("ahmet@gmail.com") is None
    _no_egress(transport)


# --- native routes ---------------------------------------------------------------------------------

@pytest.fixture
def native(make_user, monkeypatch):
    user = make_user("fs-egress-" + uuid.uuid4().hex[:8])

    def headers(key=None):
        monkeypatch.setattr(mobile_auth, "authenticate_access", lambda raw: mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))
        out = {"Authorization": "Bearer opaque-test"}
        if key:
            out["Idempotency-Key"] = key
        return out
    return user, headers


@pytest.mark.parametrize("query, rule", [(q, r) for q, r in BLOCK if 2 <= len(q.strip()) <= 100
                                         and q.strip() == q])
def test_route_withholds_with_the_existing_empty_envelope(
        raw_client, native, transport, slots, query, rule, caplog):
    caplog.set_level(logging.DEBUG)
    response = raw_client.get(PREFIX + "/foods/search", query_string={"q": query},
                              headers=native[1]())
    assert response.status_code == 200
    assert response.get_json() == {"foods": []}
    assert response.headers["Cache-Control"] == "no-store"
    _no_egress(transport)
    assert slots == []
    withheld = [r.getMessage() for r in caplog.records if "food_query_withheld" in r.getMessage()]
    assert len(withheld) == 1 and f"rule={rule} request_id=" in withheld[0]
    if len(query) >= 4:   # shorter numeric drafts collide with the request log's client IP
        assert query not in caplog.text
    for fragment in ("ahmet", "ayse", "0532", "4111", "doctor", "diabetes", "example", "gmail"):
        if fragment in query.lower():
            assert fragment not in caplog.text.lower(), fragment


def test_route_allowed_query_keeps_the_existing_contract(raw_client, native, transport, slots):
    response = raw_client.get(PREFIX + "/foods/search", query_string={"q": "tavuk göğsü"},
                              headers=native[1]())
    assert response.status_code == 200
    assert response.get_json() == {"foods": [{"provider": "fatsecret", "food_id": "35755",
                                              "name": "Chicken Breast", "brand": ""}]}
    assert slots == [1] and len(transport.posts) == 1
    assert transport.gets[0][1] == {"method": "foods.search", "search_expression": "tavuk göğsü",
                                    "format": "json", "max_results": 8}


@pytest.mark.parametrize("query", ["a", "x" * 101, "  "])
def test_route_length_check_is_unchanged(raw_client, native, transport, query):
    response = raw_client.get(PREFIX + "/foods/search", query_string={"q": query},
                              headers=native[1]())
    assert response.status_code == 400 and response.get_json()["error"]["code"] == "INVALID_FOOD_QUERY"
    _no_egress(transport)


@pytest.mark.parametrize("segment", [
    "abc", "0", "00123", "-1", "+35755", "3.5", "%2035755", "35755%20", "35755%0A",
    "%D9%A3%D9%A5%D9%A7", "%EF%BC%93", "9223372036854775808", "1" * 20, "john%20doe",
    "a%40b.co", "35755abc",
])
def test_servings_route_invalid_id_is_the_existing_404(raw_client, native, transport, slots, segment):
    response = raw_client.get(PREFIX + f"/foods/fatsecret/{segment}/servings", headers=native[1]())
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "FOOD_NOT_FOUND"
    _no_egress(transport)
    assert slots == []


def test_servings_route_overlong_id_keeps_the_existing_400(raw_client, native, transport):
    response = raw_client.get(PREFIX + "/foods/fatsecret/" + "1" * 129 + "/servings",
                              headers=native[1]())
    assert response.status_code == 400 and response.get_json()["error"]["code"] == "INVALID_FOOD_ID"
    _no_egress(transport)


def test_servings_route_valid_id_is_unchanged(raw_client, native, transport, slots):
    response = raw_client.get(PREFIX + "/foods/fatsecret/35755/servings", headers=native[1]())
    assert response.status_code == 200
    body = response.get_json()["food"]
    assert body["food_id"] == "35755" and body["servings"][0]["serving_id"] == "50321"
    assert slots == [1]
    assert transport.gets == [(fatsecret.FATSECRET_API_URL, {
        "method": "food.get.v4", "food_id": "35755", "format": "json"})]


def _save(raw_client, headers, food_id, serving_id="50321"):
    return raw_client.post(PREFIX + "/logs", headers=headers, json={
        "kind": "provider_backed", "provider": "fatsecret", "food_id": food_id,
        "serving_id": serving_id, "quantity": 1, "slot": "ogle", "discovery_source": "search"})


@pytest.mark.usefixtures("provider_persistence_permitted")
@pytest.mark.parametrize("food_id", ["john doe", "0", "00123", "٣٥٧", "35 755", "9223372036854775808",
                                     "+35755", "35755.0"])
def test_provider_save_with_a_non_canonical_id_is_404_without_egress(
        raw_client, native, transport, slots, food_id):
    response = _save(raw_client, native[1]("fs-egress-" + uuid.uuid4().hex), food_id)
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "FOOD_NOT_FOUND"
    assert MealLog.query.count() == 0
    _no_egress(transport)
    assert slots == []


@pytest.mark.usefixtures("provider_persistence_permitted")
def test_save_command_trimming_is_unchanged_and_only_the_canonical_id_leaves(
        raw_client, native, transport):
    # Pre-existing contract: parse_provider_identity trims the command's identity
    # before the policy sees it; only the canonical value reaches the provider.
    response = _save(raw_client, native[1]("fs-egress-" + uuid.uuid4().hex), " 35755 ")
    assert response.status_code == 201
    assert [g[1]["food_id"] for g in transport.gets] == ["35755"]


@pytest.mark.usefixtures("provider_persistence_permitted")
def test_provider_save_refetches_once_and_an_idempotent_replay_makes_no_request(
        raw_client, native, transport):
    key = "fs-egress-replay-" + uuid.uuid4().hex
    first = _save(raw_client, native[1](key), "35755")
    assert first.status_code == 201, first.get_json()
    assert [g[1] for g in transport.gets] == [
        {"method": "food.get.v4", "food_id": "35755", "format": "json"}]
    replay = _save(raw_client, native[1](key), "35755")
    assert replay.status_code == 200 and replay.get_json() == first.get_json()
    assert len(transport.gets) == 1 and MealLog.query.count() == 1


def test_live_names_skip_a_non_canonical_stored_id_without_spending_budget(app, monkeypatch):
    fetched = []
    monkeypatch.setattr(provider_retention, "_cached_food", lambda food_id: None)
    monkeypatch.setattr(provider_retention, "_remember_food", lambda *a: None)
    monkeypatch.setattr(provider_retention, "_fetch_food", lambda food_id: fetched.append(food_id) or {
        "name": "Chicken Breast", "servings": {"50321": "100 g"}})
    names = provider_retention.LiveNames(max_lookups=1)
    with app.app_context():
        bad = names._render(provider_retention.provider_component("x1", "50321", 1))
        assert names._lookups == 1 and names._failed is False
        good = names._render(provider_retention.provider_component("35755", "50321", 1))
    assert bad == f"{provider_retention.PLACEHOLDER} (1x)"
    assert good == "Chicken Breast (1x 100 g)"
    assert fetched == ["35755"] and names._lookups == 0


def test_live_names_refresh_of_a_non_canonical_id_makes_no_provider_request(app, transport, monkeypatch):
    monkeypatch.setattr(provider_retention, "_redis", lambda: None)
    with app.app_context():
        assert provider_retention._fetch_food("x1") is None
    _no_egress(transport)


def test_barcode_cache_with_a_legacy_non_canonical_id_is_a_miss(app, transport, slots):
    from app.extensions import db
    from app.models import BarcodeFoodCache
    with app.app_context():
        db.session.add(BarcodeFoodCache(barcode="0012345678905", food_id="legacy-1",
                                        food_name="Legacy", brand="", payload={}))
        db.session.commit()
        assert mobile_food_discovery.barcode_lookup("0012345678905") is None
    _no_egress(transport)
    assert slots == []


@pytest.mark.parametrize("query", ["ahmet@gmail.com", "tel 0532 123 45 67", "my doctor said"])
def test_discovery_search_is_screened_before_the_slot_for_any_caller(app, transport, slots, query):
    # Defence in depth behind the route: a direct caller gets the same empty
    # answer with no concurrency slot, token or request.
    with app.test_request_context():
        assert mobile_food_discovery.search(query) == []
    _no_egress(transport)
    assert slots == []


def test_discovery_search_allowed_query_takes_one_slot(app, transport, slots):
    with app.test_request_context():
        assert [f["food_id"] for f in mobile_food_discovery.search("simit")] == ["35755"]
    assert slots == [1] and len(transport.gets) == 1


# === Pre-QA technical stabilization (#547 c7242035071), T1–T6 ===================================
# Every value is synthetic. Each egress test uses the fake transport above, so
# a withheld or rejected input must leave ZERO token POSTs and ZERO GETs.

# --- T1: the screening work is bounded before normalisation or any regex ----------------------

@pytest.mark.parametrize("query, rule", [
    ("a" * 100, None), ("a" * 101, "length"),
    ("ş" * 100, None), ("ş" * 101, "length"),
    ("İ" * 100, None), ("İ" * 101, "length"),          # folding changes length; the bound does not
    ("🍎" * 100, "no_letter"), ("🍎" * 101, "length"),  # astral: one code point each
    (" " * 101, "length"), ("tavuk " * 20, "length"),
    ("ahmet@gmail.com " + "a" * 90, "length"),          # long + trigger: still one label, no text
    (("tavuk göğsü " * 9)[:100], None),
    (b"x" * 1000, "control"), (["x"] * 1000, "control"),
])
def test_length_bound(query, rule):
    assert policy.withheld_search_rule(query) == rule


def test_length_bound_is_the_native_contract():
    assert policy.MAX_QUERY_CHARS == 100
    assert policy.RULES[0] == policy.LENGTH == "length"


@pytest.mark.parametrize("make", [
    lambda n: "a-" * n,                       # the quadratic _URL shape (assessment P3-1)
    lambda n: "a." * n,
    lambda n: "1 " * n,
    lambda n: "\ufdfa" * n,                  # U+FDFA: the largest NFKC expansion (18 code points)
    lambda n: "ﬃ" * n,
    lambda n: "​a" * n,                  # Cf stripped during the fold
])
def test_adversarial_input_cost_is_bounded(make):
    # Before T1, 'a-' * 25000 took ~17.7 s. Over the bound it is withheld
    # before any normalisation; at the bound the work is at most 100 code points.
    started = time.perf_counter()
    assert policy.withheld_search_rule(make(25000)) == "length"
    assert policy.withheld_search_rule(make(10 ** 6)) == "length"
    over = time.perf_counter() - started
    at_bound = make(100)[:100]
    started = time.perf_counter()
    for _ in range(200):
        rule = policy.withheld_search_rule(at_bound)
    per_call = (time.perf_counter() - started) / 200
    assert rule is None or rule in policy.RULES
    # Generous wall-clock limits for loaded CI runners; base took seconds.
    assert over < 0.5, over
    assert per_call < 0.02, per_call


@pytest.mark.parametrize("strict", [True, False])
def test_overlong_service_query_makes_no_token_or_request(app, transport, strict, caplog):
    caplog.set_level(logging.DEBUG)
    query = "mercimek çorbası " * 7
    with app.app_context():
        assert fatsecret._food_search_raw(query, strict=strict) == ([] if strict else None)
        assert fatsecret._fs_search_foods(query, "synthetic-token") == []
    _no_egress(transport)
    assert "rule=length" in caplog.text and "mercimek" not in caplog.text


def test_overlong_discovery_search_takes_no_slot(app, transport, slots):
    with app.app_context():
        assert mobile_food_discovery.search("x" * 101) == []
    _no_egress(transport)
    assert slots == []


def test_backstop_withholds_an_overlong_search_expression(app, transport):
    with app.app_context():
        with pytest.raises(fatsecret.ProviderRequestWithheld):
            fatsecret._fs_get(fatsecret.FATSECRET_API_URL, params={
                "method": "foods.search", "search_expression": "a" * 101})
    _no_egress(transport)


@pytest.mark.parametrize("query", [("tavuk göğsü " * 9)[:100], "x" * 100, "ş" * 100])
def test_query_at_the_bound_is_sent_byte_identical(app, transport, query):
    with app.app_context():
        assert fatsecret._food_search_raw(query, strict=True)
    assert transport.gets == [(fatsecret.FATSECRET_API_URL, {
        "method": "foods.search", "search_expression": query, "format": "json", "max_results": 8})]


# --- T2: the transport backstop fails closed on unsupported representations ------------------

class _LyingInt(int):
    def __str__(self):
        return "ahmet@gmail.com"


class _LyingStr(str):
    def encode(self, *args, **kwargs):
        return b"ahmet@gmail.com"


@pytest.mark.parametrize("params", [
    {b"food_id": b"abc"}, {b"food_id": "abc"}, {b"food_id": "35755"},
    {"method": "foods.search", b"search_expression": "ahmet@gmail.com"},
    [(b"food_id", "abc")], [("method", "food.get"), (b"food_id", "x")],
    {1: "x"}, {None: "x"}, {("food_id",): "abc"},
    {"FOOD_ID": "abc"}, {"Food_Id": "35755"}, {"food_id ": "abc"}, {" search_expression": "x"},
    {"ｆｏｏｄ_ｉｄ": "abc"}, {"SEARCH_EXPRESSION": "ahmet@gmail.com"},
    {"method": ["foods.search", "food.get"]}, {"method": b"food.get"}, {"method": None},
    {"max_results": True}, {"max_results": 8.0}, {"method": {"a": 1}},
    {"food_id": b"35755"}, {"search_expression": b"tavuk"}, {"food_id": 35755.0},
    (pair for pair in [("food_id", "abc")]), {("food_id", "abc")},
    iter([("method", "food.get")]), [["food_id", "abc", "extra"]], ["food_id"], [("food_id", "abc")],
    {"food_\u200bid": "abc"}, {"food_id\x00": "abc"}, {"\u200bsearch_expression": "x"},
    {"food_id": _LyingInt(35755)}, {"search_expression": _LyingStr("tavuk")},
    {"method": _LyingStr("food.get")}, {"max_results": _Big.ID},
])
def test_backstop_rejects_unsupported_keys_values_and_shapes(app, transport, params):
    with app.app_context():
        with pytest.raises(fatsecret.ProviderRequestWithheld):
            fatsecret._fs_get(fatsecret.FATSECRET_API_URL, params=params)
    _no_egress(transport)


@pytest.mark.parametrize("url", [
    fatsecret.FATSECRET_API_URL.encode() + b"?food_id=abc",
    fatsecret.FATSECRET_API_URL.encode(),
    fatsecret.FATSECRET_API_URL + "?FOOD_ID=abc",
    fatsecret.FATSECRET_API_URL + "?food%5Fid=abc",          # percent-encoded name decodes to food_id
    fatsecret.FATSECRET_API_URL + "?search_expression=" + "a" * 101,
    fatsecret.FATSECRET_API_URL + "?Search_Expression=a%40b",
    fatsecret.FATSECRET_API_URL + "?food_id%00=abc",
    fatsecret.FATSECRET_API_URL + "?food_%E2%80%8Bid=abc",
])
def test_backstop_rejects_alternate_url_query_representations(app, transport, url):
    with app.app_context():
        with pytest.raises(fatsecret.ProviderRequestWithheld):
            fatsecret._fs_get(url, params={"method": "food.get"})
    _no_egress(transport)


@pytest.mark.parametrize("body", [{"data": {"search_expression": "ahmet@gmail.com"}},
                                  {"data": "food_id=abc"}, {"json": {"food_id": "abc"}},
                                  {"files": {"f": b"x"}}])
def test_backstop_refuses_a_get_body(app, transport, body):
    with app.app_context():
        with pytest.raises(fatsecret.ProviderRequestWithheld):
            fatsecret._fs_get(fatsecret.FATSECRET_API_URL, params={"method": "food.get"}, **body)
    _no_egress(transport)


@pytest.mark.parametrize("params", [
    [("method", "foods.search"), ("search_expression", "tavuk göğsü"), ("max_results", 5)],
    (("method", "food.get.v4"), ("food_id", "35755"), ("format", "json")),
    [["method", "food.get.v4"], ["food_id", 35755]],
    {"method": "food.find_id_for_barcode", "barcode": "0012345678905", "format": "json"},
])
def test_backstop_keeps_supported_pair_list_and_mapping_shapes(app, transport, params):
    with app.app_context():
        fatsecret._fs_get(fatsecret.FATSECRET_API_URL, params=params)
    assert transport.gets == [(fatsecret.FATSECRET_API_URL, dict(params))]


def test_requests_serializes_valid_params_byte_identically():
    # The wire bytes a supported shape produces are unchanged (requests' own encoder).
    import requests
    for params, query in [
        ({"method": "foods.search", "search_expression": "tavuk göğsü", "format": "json",
          "max_results": 8},
         "method=foods.search&search_expression=tavuk+g%C3%B6%C4%9Fs%C3%BC&format=json&max_results=8"),
        ({"method": "food.get.v4", "food_id": "35755", "format": "json"},
         "method=food.get.v4&food_id=35755&format=json"),
        ({"method": "food.get.v4", "food_id": 35755, "format": "json"},
         "method=food.get.v4&food_id=35755&format=json"),
    ]:
        prepared = requests.Request("GET", fatsecret.FATSECRET_API_URL, params=params).prepare()
        assert prepared.url.split("?", 1)[1] == query
    # Why bytes names are refused: requests sends them as given, unscreened.
    prepared = requests.Request("GET", fatsecret.FATSECRET_API_URL,
                                params={b"food_id": "abc"}).prepare()
    assert prepared.url.endswith("?food_id=abc")


# --- T3: Turkish diabetes term, inside the first-person + health conjunction only -------------

@pytest.mark.parametrize("query", [
    "diyabetim var ben ne yiyeyim", "benim diyabetim var", "DİYABETİM VAR BENİM",
    "ben diyabet hastasıyım", "annem diyabet hastası tatlı", "babam DIYABETLI ne yesin",
    "Bende diyabet var", "oğlum diyabetli kahvaltı", "i have diyabet",
])
def test_first_person_turkish_diabetes_is_withheld(query):
    assert policy.withheld_search_rule(query) == "first_person_health"


@pytest.mark.parametrize("query", [
    "diyabetik çikolata", "diyabet tatlısı", "Diyabetik Bisküvi", "DİYABETİK reçel",
    "diyabete uygun ekmek", "şekersiz diyabetik gofret", "Ülker diyabetik", "diyabet dostu kek",
    "DIYABETIK SUT",
])
def test_ordinary_diabetes_food_searches_pass(query):
    assert policy.withheld_search_rule(query) is None


def test_suffix_only_first_person_stays_a_documented_residual():
    # No first-person marker word: the approved conjunction does not fire.
    assert policy.withheld_search_rule("diyabetliyim") is None


@pytest.mark.parametrize("strict", [True, False])
def test_turkish_diabetes_statement_makes_no_egress(app, transport, strict, caplog):
    caplog.set_level(logging.DEBUG)
    with app.app_context():
        assert fatsecret._food_search_raw("diyabetim var ben ne yiyeyim",
                                          strict=strict) == ([] if strict else None)
    _no_egress(transport)
    assert "diyabet" not in caplog.text


# --- T4: order of checks with the release switch closed (the runtime default) ---------------

@pytest.fixture
def closed_transport(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(fatsecret, "_get_fs_session", lambda: session)
    monkeypatch.setitem(fatsecret._fs_token_cache, "token", "warm-synthetic-token")
    monkeypatch.setitem(fatsecret._fs_token_cache, "expires_at", 10 ** 12)
    monkeypatch.setenv("FATSECRET_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("FATSECRET_CLIENT_SECRET", "synthetic-secret")
    return session


def test_service_policy_checks_run_before_the_switch_but_nothing_leaves(app, closed_transport, caplog):
    from app.services import launch_food_search
    caplog.set_level(logging.DEBUG)
    with app.app_context():
        assert not launch_food_search.available()
        # Layer C: the local check runs first and logs its label only.
        assert fatsecret._food_search_raw("ahmet@gmail.com") is None
        assert "rule=email_or_handle" in caplog.text
        assert fatsecret._food_get_raw("abc") is None
        assert "rule=non_canonical_food_id" in caplog.text
        # Layer D: a valid query reaches the token gate, which refuses (warm cache too).
        assert fatsecret._food_search_raw("tavuk göğsü") is None
        with pytest.raises(fatsecret.FoodProviderUnavailable):
            fatsecret._food_search_raw("tavuk göğsü", strict=True)
        assert fatsecret._food_get_raw("35755") is None
        # Layer B: discovery checks the switch before its policy check.
        with pytest.raises(launch_food_search.FoodSearchExcluded):
            mobile_food_discovery.search("ahmet@gmail.com")
        # Layer E: the transport refuses before the backstop sees the params.
        with pytest.raises(launch_food_search.FoodSearchExcluded):
            fatsecret._fs_get(fatsecret.FATSECRET_API_URL, params={b"food_id": "abc"})
    _no_egress(closed_transport)
    assert "ahmet" not in caplog.text


# --- T5: legacy callers cannot take a token in the target release, even with the flag ON -----

@pytest.mark.parametrize("path", [
    "/api/food/search?q=tavuk", "/api/food/35755/servings", "/api/food/servings-by-name?name=tavuk",
    "/api/food/barcode?code=5000159407236", "/api/social/messages", "/",
])
def test_legacy_callers_cannot_take_a_token_with_the_flag_on(app, closed_transport, path):
    from app.services import fatsecret_boundary, launch_food_search
    app.config[launch_food_search.FLAG_KEY] = True
    try:
        with app.test_request_context(path):
            assert launch_food_search.released() and not launch_food_search.available()
            assert not fatsecret_boundary.uncovered_provider_persistence_allowed()
            with pytest.raises(launch_food_search.FoodSearchExcluded):
                fatsecret._get_fatsecret_token()
            assert fatsecret._food_find_by_barcode("0012345678905") is None
            assert fatsecret._food_search_fatsecret("tavuk") is None
        with app.app_context():                           # a worker: no request context
            with pytest.raises(launch_food_search.FoodSearchExcluded):
                fatsecret._get_fatsecret_token()
    finally:
        app.config.pop(launch_food_search.FLAG_KEY, None)
    _no_egress(closed_transport)


def test_flag_on_native_endpoint_is_the_only_token_scope(app, closed_transport):
    # Control for the test above: the gate is endpoint-scoped, not simply closed.
    from app.services import launch_food_search
    app.config[launch_food_search.FLAG_KEY] = True
    try:
        with app.test_request_context(PREFIX + "/foods/search?q=tavuk"):
            assert launch_food_search.available()
            assert fatsecret._get_fatsecret_token() == "warm-synthetic-token"
    finally:
        app.config.pop(launch_food_search.FLAG_KEY, None)
    _no_egress(closed_transport)


def test_legacy_browser_routes_stay_closed_with_the_flag_on(app, raw_client, closed_transport):
    from app.services import launch_food_search
    app.config[launch_food_search.FLAG_KEY] = True
    try:
        for path in ("/api/food/servings-by-name?name=ahmet@gmail.com",
                     "/api/food/search?q=diyabetim var ben", "/api/food/x-1/servings"):
            response = raw_client.get(path)
            assert response.status_code == 404
            assert response.get_json()["error"]["code"] == launch_food_search.ERROR_CODE
    finally:
        app.config.pop(launch_food_search.FLAG_KEY, None)
    _no_egress(closed_transport)


# --- T6: the legacy browser servings route never logs its raw path argument -----------------

@pytest.mark.parametrize("segment", ["ahmet@gmail.com", "05321234567", "john doe", "x-1", "00123"])
def test_browser_servings_route_does_not_log_a_rejected_id(client, auth_user, transport, segment, caplog):
    caplog.set_level(logging.DEBUG)
    response = client.get(f"/api/food/{segment}/servings")
    assert response.status_code == 200
    assert response.get_json() == {"servings": [], "debug": "no_servings_returned"}
    _no_egress(transport)
    messages = [r.getMessage() for r in caplog.records]
    assert any("food_servings event=called id_state=non_canonical" in m for m in messages)
    assert any("food_servings event=no_servings id_state=non_canonical" in m for m in messages)
    assert not any(segment in m for m in messages), [m for m in messages if segment in m]


def test_browser_servings_route_valid_id_keeps_its_response(client, auth_user, transport, caplog):
    caplog.set_level(logging.DEBUG)
    response = client.get("/api/food/35755/servings")
    assert response.status_code == 200
    servings = response.get_json()["servings"]
    assert servings and servings[0]["serving_description"] == "100 g"
    messages = [r.getMessage() for r in caplog.records if "food_servings event=" in r.getMessage()]
    assert messages == ["food_servings event=called id_state=canonical",
                        "food_servings event=ok id_state=canonical serving_count=1"]
