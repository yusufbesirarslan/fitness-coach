"""Coach capability request + disclosure availability (#533 companion D-1/D-2).

SQLite layer. The D1 state read is faked at ``_transaction``/``_state``/
``_check_operation``/``_require_grant_cohort`` (``ai_consent._transaction``
refuses any dialect but PostgreSQL); everything above it — the request
predicate, publication, the closed tool, dispatch, the turn effect, the wire
projection and the mobile route — runs for real. Real owner-lock grant,
revoke and cohort behaviour is in tests/test_coach_capability_request_pg.py.
Every capability contract here is SYNTHETIC; nothing is approved in source.
"""
import json
from contextlib import contextmanager

import pytest

from app.services import (
    ai_coach, ai_consent, ai_consent_disclosures, coach_capabilities as caps,
    coach_plan_tools, coach_privacy, mobile_coach,
)
from app.services.coach_plan_tools import capability_request as request_tool
from app.services.coach_plan_tools import nutrition
from tests.test_coach_capability_authority import (
    CAP_VERSION, NCAP_VERSION, NUTRITION_PLAN, R2, _record, _register,
)

TOOL = request_tool.REQUEST_CAPABILITY_TOOL
TRAINING = caps.TRAINING_PLAN_WRITE
NUTRITION = caps.NUTRITION_PLAN_ACCESS
OWNER = 7
TRAINING_WIRE = {"capability": "training_plan_write", "purpose": "coach.training_plan_edit"}
NUTRITION_WIRE = {"capability": "nutrition_plan_access", "purpose": "coach.nutrition_plan_access"}


class FakeD1:
    """The owner's committed consent state, as ``_state`` would read it."""

    def __init__(self, monkeypatch, *, generation=5, coach=True, **grants):
        self.generation, self.coach, self.grants = generation, coach, dict(grants)
        self.ineligible, self.reads = set(), 0

        @contextmanager
        def transaction(owner):
            self.reads += 1
            yield object()

        def check_operation(state, authority, conn=None):
            if state["generation"] != authority.generation:
                raise ai_consent.ConsentDenied("AI_CONSENT_CHANGED")
            if not self.coach:
                raise ai_consent.ConsentDenied("AI_CONSENT_REQUIRED")

        def cohort(conn, owner, purpose):
            if purpose in self.ineligible:
                raise ai_consent.ConsentDenied("AI_CONSENT_PURPOSE_UNAVAILABLE_FOR_ACCOUNT", 403)

        monkeypatch.setattr(ai_consent, "_transaction", transaction)
        monkeypatch.setattr(ai_consent, "_state", lambda conn, owner: {
            "owner_id": owner, "generation": self.generation, "grants": dict(self.grants)})
        monkeypatch.setattr(ai_consent, "_check_operation", check_operation)
        monkeypatch.setattr(ai_consent, "_require_grant_cohort", cohort)

    def grant(self, purpose, version):
        self.grants[purpose] = _record(version)
        self.generation += 1

    def revoke(self, purpose):
        self.grants[purpose] = dict(self.grants[purpose], state="revoked",
                                    revoked_at="2026-11-28T00:00:00")
        self.generation += 1


def _operation(owner=OWNER, generation=5, feature="coach", version=R2):
    return ai_consent.operation_scope(
        ai_consent.OperationAuthority(owner, feature, generation, version))


def _open(capability, owner=OWNER, **operation):
    with _operation(**operation):
        return ai_consent.capability_request_open(owner, capability)


@pytest.fixture
def training(monkeypatch):
    _register(monkeypatch, TRAINING, CAP_VERSION)


@pytest.fixture
def nutrition_cap(monkeypatch):
    _register(monkeypatch, NUTRITION, NCAP_VERSION)
    monkeypatch.setattr(nutrition, "APPROVED_NUTRITION_READ_DISCLOSURES",
                        frozenset({(R2, NUTRITION_PLAN)}))


@pytest.fixture
def flag_on(app):
    previous = app.config.get(coach_plan_tools.FLAG_KEY, False)
    app.config[coach_plan_tools.FLAG_KEY] = True
    yield
    app.config[coach_plan_tools.FLAG_KEY] = previous


@pytest.fixture
def coach_turn(app):
    """A Coach turn with the user's own text, inside a request context."""
    from app.observability import assign_request_id

    @contextmanager
    def turn(message="change my bench press to 4 sets", owner=OWNER, generation=5):
        with app.test_request_context("/api/v1/coach/messages", method="POST"):
            assign_request_id()
            coach_plan_tools.begin_turn(message)
            with _operation(owner=owner, generation=generation):
                yield
    return turn


def _names(defs):
    return [d["name"] for d in defs]


# ── The request predicate: server-owned, fail closed ─────────────────────

def test_closed_source_offers_nothing_and_reads_nothing(monkeypatch):
    monkeypatch.setattr(ai_consent, "_transaction",
                        lambda *a, **k: pytest.fail("closed source must not read D1"))
    for capability in (*caps.SPECS, "coach.conversation", None, 3):
        assert _open(capability) is False


def test_missing_capability_is_requested_once_granted_never_again(monkeypatch, training):
    d1 = FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    assert _open(TRAINING) is True
    d1.grant("coach.training_plan_edit", CAP_VERSION)
    assert _open(TRAINING, generation=d1.generation) is False  # no repeated consent
    d1.revoke("coach.training_plan_edit")
    assert _open(TRAINING, generation=d1.generation) is True  # withdraw → ask again


@pytest.mark.parametrize("mutation", [
    {"approve": False}, {"launch": False},
    {"categories": ("training_plan_content",)},
    {"recipients": (("bedrock", "eu.anthropic.claude-sonnet-5-5"),)},
])
def test_unpublished_or_unapproved_contract_is_never_requested(monkeypatch, mutation):
    _register(monkeypatch, TRAINING, CAP_VERSION, **mutation)
    FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    assert _open(TRAINING) is False


def test_edited_text_is_never_requested(monkeypatch, training):
    FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    published = dict(ai_consent_disclosures.PUBLISHED_TEXTS)
    published[(CAP_VERSION, "tr")] = "edited after review\n"
    monkeypatch.setattr(ai_consent_disclosures, "PUBLISHED_TEXTS", published)
    assert _open(TRAINING) is False


def test_generation_owner_feature_and_coach_grant_are_respected(monkeypatch, training):
    d1 = FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    assert _open(TRAINING) is True
    assert ai_consent.capability_request_open(OWNER, TRAINING) is False  # no operation
    assert _open(TRAINING, owner=OWNER + 1) is False  # another account's operation
    assert _open(TRAINING, feature="nutrition_plan") is False
    d1.generation += 1  # a consent change after the operation was captured
    assert _open(TRAINING) is False
    d1.generation, d1.coach = 5, False
    assert _open(TRAINING) is False


def test_ineligible_account_is_never_offered_an_allow_it_cannot_use(monkeypatch, training):
    d1 = FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    d1.ineligible.add("coach.training_plan_edit")
    assert _open(TRAINING) is False


def test_outage_fails_closed(monkeypatch, training):
    @contextmanager
    def down(owner):
        raise ai_consent.ConsentDenied("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503)
        yield  # pragma: no cover
    monkeypatch.setattr(ai_consent, "_transaction", down)
    assert _open(TRAINING) is False


def test_nutrition_respects_the_separate_nutrition_plan_grant(monkeypatch, nutrition_cap):
    d1 = FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    assert _open(NUTRITION) is True  # both missing: the client asks nutrition.plan first
    d1.grant("nutrition.plan", NUTRITION_PLAN)
    assert _open(NUTRITION, generation=d1.generation) is True  # capability still missing
    d1.grant("coach.nutrition_plan_access", NCAP_VERSION)
    assert _open(NUTRITION, generation=d1.generation) is False
    d1.revoke("nutrition.plan")  # capability held, underlying grant withdrawn
    assert _open(NUTRITION, generation=d1.generation) is True


def test_nutrition_plan_missing_and_unpublished_is_not_actionable(monkeypatch, nutrition_cap):
    FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    launched = dict(ai_consent_disclosures.LAUNCH_VERSIONS)
    launched.pop("nutrition.plan")
    monkeypatch.setattr(ai_consent_disclosures, "LAUNCH_VERSIONS", launched)
    assert _open(NUTRITION) is False


def test_nutrition_plan_ineligible_is_not_actionable(monkeypatch, nutrition_cap):
    d1 = FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    d1.ineligible.add("nutrition.plan")
    assert _open(NUTRITION) is False


def test_training_capability_never_needs_or_asks_for_nutrition_plan(monkeypatch, training):
    d1 = FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    d1.ineligible.add("nutrition.plan")
    assert _open(TRAINING) is True


# ── D-2: disclosure availability ─────────────────────────────────────────

def _offered():
    return {d["purpose"]: d["version"] for d in ai_consent_disclosures.available_disclosures()}


def test_registered_but_unapproved_capability_is_not_published(monkeypatch):
    core = _offered()
    _register(monkeypatch, TRAINING, CAP_VERSION, approve=False)
    _register(monkeypatch, NUTRITION, NCAP_VERSION, approve=False)
    assert _offered() == core  # registered + launched + texts, still not published
    assert set(core) == {"training.plan", "coach.conversation", "nutrition.plan",
                         "pump_check.analysis", "qr_menu.analysis"}


def test_approved_capability_is_published_and_core_purposes_unchanged(monkeypatch):
    core = _offered()
    _register(monkeypatch, TRAINING, CAP_VERSION)
    offered = _offered()
    assert offered.pop("coach.training_plan_edit") == CAP_VERSION
    assert offered == core


def test_unapproved_capability_grant_is_still_refused(monkeypatch, app):
    policy = _register(monkeypatch, TRAINING, CAP_VERSION, approve=False)
    assert not caps.grant_open(policy)


# ── Publication of the closed tool ───────────────────────────────────────

def test_tool_is_absent_in_this_source_and_the_tool_list_is_unchanged(
        app, flag_on, coach_turn, monkeypatch):
    FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    with coach_turn():
        assert request_tool.published_tool_defs(OWNER) == ()
        assert coach_plan_tools.turn_plan_effects(OWNER).get("capability_request") is None


def test_flag_off_publishes_nothing(app, coach_turn, monkeypatch, training):
    FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    app.config[coach_plan_tools.FLAG_KEY] = False
    with coach_turn():
        assert request_tool.published_tool_defs(OWNER) == ()


def test_tool_lists_only_actionable_capabilities(app, flag_on, coach_turn, monkeypatch,
                                                  training, nutrition_cap):
    d1 = FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    with coach_turn():
        (definition,) = request_tool.published_tool_defs(OWNER)
    assert definition["name"] == TOOL
    assert definition["parameters"] == {
        "type": "object", "properties": {"capability": {
            "type": "string", "enum": [TRAINING, NUTRITION]}},
        "required": ["capability"], "additionalProperties": False}
    d1.grant("coach.training_plan_edit", CAP_VERSION)
    with coach_turn(generation=d1.generation):
        (definition,) = request_tool.published_tool_defs(OWNER)
    assert definition["parameters"]["properties"]["capability"]["enum"] == [NUTRITION]
    d1.grant("nutrition.plan", NUTRITION_PLAN)
    d1.grant("coach.nutrition_plan_access", NCAP_VERSION)
    with coach_turn(generation=d1.generation):
        assert request_tool.published_tool_defs(OWNER) == ()


def test_nutrition_is_offered_only_when_a_nutrition_tool_could_open(
        app, flag_on, coach_turn, monkeypatch):
    _register(monkeypatch, NUTRITION, NCAP_VERSION)  # no READ pair, C1 edit gate closed
    FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    with coach_turn():
        assert request_tool.published_tool_defs(OWNER) == ()


@pytest.mark.parametrize("message", ["", "   ", "evet", "yes", "hayır"])
def test_no_tool_without_user_text_or_on_a_confirm_cancel_answer(
        app, flag_on, coach_turn, monkeypatch, training, message):
    FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    with coach_turn(message=message):
        assert request_tool.published_tool_defs(OWNER) == ()


def test_the_provider_tool_list_swaps_the_write_family_for_the_request(
        app, flag_on, coach_turn, monkeypatch, training):
    """r2 only: no Training write tool, exactly one request tool; with the
    grant: the write family, no request tool."""
    d1 = FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    monkeypatch.setattr(ai_consent, "authorize_operation", lambda _a: None)
    with coach_turn():
        names = _names(ai_coach._coach_tool_defs_for_call(OWNER))
    assert TOOL in names
    assert not set(names) & coach_plan_tools.PLAN_WRITE_TOOL_NAMES
    d1.grant("coach.training_plan_edit", CAP_VERSION)
    with coach_turn(generation=d1.generation):
        names = _names(ai_coach._coach_tool_defs_for_call(OWNER))
    assert TOOL not in names
    assert coach_plan_tools.PLAN_MUTATION_TOOL_NAMES <= set(names)


# ── Execution: records a review offer, never authority ───────────────────

def _dispatch(arguments):
    raw = ai_coach._dispatch_coach_tool(OWNER, TOOL, json.dumps(arguments))
    return json.loads(raw), json.loads(coach_privacy.tool_result(TOOL, raw, user_id=OWNER))


def test_executing_records_the_request_and_changes_nothing(
        app, flag_on, coach_turn, monkeypatch, training):
    FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    monkeypatch.setattr(ai_consent, "authorize_operation", lambda _a: None)
    monkeypatch.setattr(ai_consent, "mutate", lambda *a, **k: pytest.fail("never grants"))
    with coach_turn():
        raw, seen = _dispatch({"capability": TRAINING})
        assert raw == {"status": "ok", "outcome": "review_offered"}
        assert seen == {"status": "ok", "outcome": "review_offered"}  # closed projection
        effects = coach_plan_tools.turn_plan_effects(OWNER)
        assert effects["capability_request"] == TRAINING_WIRE
        assert effects["changed"] is False and effects["pending_confirmation"] is False
        assert not coach_plan_tools.plan_changed_this_turn()
        assert not coach_plan_tools.proposal_created_this_turn()
        assert not coach_plan_tools.plan_tool_attempted_this_turn()


@pytest.mark.parametrize("arguments", [
    {}, {"capability": "coach.training_plan_edit"}, {"capability": "nutrition.plan"},
    {"capability": TRAINING, "purpose": "coach.training_plan_edit"},
    {"capability": "admin"}, {"capability": NUTRITION},  # not actionable here
])
def test_any_other_argument_is_unavailable_and_records_nothing(
        app, flag_on, coach_turn, monkeypatch, training, arguments):
    FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    with coach_turn():
        assert request_tool.execute(OWNER, arguments) == {
            "status": "unsupported", "outcome": "unavailable"}
        assert "capability_request" not in coach_plan_tools.turn_plan_effects(OWNER)


def test_unparsable_arguments_are_refused_not_folded(app, flag_on, coach_turn, monkeypatch, training):
    FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    monkeypatch.setattr(ai_consent, "authorize_operation", lambda _a: None)
    with coach_turn():
        raw = json.loads(ai_coach._dispatch_coach_tool(OWNER, TOOL, "{not json"))
        assert raw["status"] == "error"
        assert "capability_request" not in coach_plan_tools.turn_plan_effects(OWNER)


def test_a_model_call_on_a_granted_capability_records_nothing(
        app, flag_on, coach_turn, monkeypatch, training):
    """A stale/forged call cannot re-ask for a permission already held."""
    d1 = FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    d1.grant("coach.training_plan_edit", CAP_VERSION)
    with coach_turn(generation=d1.generation):
        assert request_tool.execute(OWNER, {"capability": TRAINING})["outcome"] == "unavailable"
        assert "capability_request" not in coach_plan_tools.turn_plan_effects(OWNER)


def test_multiple_missing_permissions_are_presented_one_at_a_time(
        app, flag_on, coach_turn, monkeypatch, training, nutrition_cap):
    FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    with coach_turn():
        request_tool.execute(OWNER, {"capability": NUTRITION})
        request_tool.execute(OWNER, {"capability": TRAINING})
        # Exactly one request per turn (the first); the next turn asks again.
        assert coach_plan_tools.turn_plan_effects(OWNER)["capability_request"] == NUTRITION_WIRE
    with coach_turn():
        assert "capability_request" not in coach_plan_tools.turn_plan_effects(OWNER)


def test_consent_change_during_the_turn_retires_the_request(
        app, flag_on, coach_turn, monkeypatch, training):
    d1 = FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    with coach_turn():
        request_tool.execute(OWNER, {"capability": TRAINING})
        d1.grant("coach.training_plan_edit", CAP_VERSION)  # granted on another device
        assert "capability_request" not in coach_plan_tools.turn_plan_effects(OWNER)
    with coach_turn():
        request_tool.execute(OWNER, {"capability": TRAINING})
        d1.coach = False  # Coach withdrawn mid-turn
        assert "capability_request" not in coach_plan_tools.turn_plan_effects(OWNER)


def test_account_switch_never_carries_a_request(app, flag_on, coach_turn, monkeypatch, training):
    FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    with coach_turn():
        request_tool.execute(OWNER, {"capability": TRAINING})
        assert request_tool.turn_effect(OWNER + 1) is None
        assert "capability_request" not in coach_plan_tools.turn_plan_effects(OWNER + 1)
    with coach_turn(owner=OWNER + 1):
        assert request_tool.execute(OWNER, {"capability": TRAINING})["outcome"] == "unavailable"


def test_grant_then_user_resend_runs_the_write_family_with_no_replay(
        app, flag_on, coach_turn, monkeypatch, training):
    """Turn 1 records the request and nothing else. After the grant, nothing
    runs until the user sends again; then the ordinary write family is
    published and the request tool is gone."""
    from app.services import plan_confirmation

    d1 = FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    monkeypatch.setattr(ai_consent, "authorize_operation", lambda _a: None)
    with coach_turn():
        request_tool.execute(OWNER, {"capability": TRAINING})
    assert plan_confirmation.get_pending(OWNER) is None  # nothing staged
    d1.grant("coach.training_plan_edit", CAP_VERSION)
    with coach_turn(generation=d1.generation):
        assert coach_plan_tools.turn_plan_effects(OWNER).get("capability_request") is None
        names = _names(ai_coach._coach_tool_defs_for_call(OWNER))
    assert TOOL not in names and coach_plan_tools.PLAN_MUTATION_TOOL_NAMES <= set(names)


def test_direct_confirmation_never_carries_a_request(app, flag_on, monkeypatch, training):
    FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    with app.test_request_context("/api/v1/coach/plan-confirmation", method="POST"):
        coach_plan_tools.begin_turn("change my bench press")
        with _operation():
            request_tool.execute(OWNER, {"capability": TRAINING})
        # The B2 route reads effects OUTSIDE any Coach operation.
        assert "capability_request" not in coach_plan_tools.turn_plan_effects(OWNER)


def test_simultaneous_nutrition_quote_and_training_request(
        app, flag_on, coach_turn, monkeypatch, training):
    quote = {"changed": False, "pending_confirmation": True, "proposal_id": "nq",
             "proposal": {"proposal_id": "nq", "change": {"kind": "add"}}}
    monkeypatch.setattr(nutrition, "turn_effects", lambda user_id, language=None: dict(quote))
    FakeD1(monkeypatch, **{"coach.conversation": _record(R2)})
    with coach_turn():
        request_tool.execute(OWNER, {"capability": TRAINING})
        effects = coach_plan_tools.turn_plan_effects(OWNER)
    wire = mobile_coach.project_plan_effects(effects)
    assert wire["nutrition_plan"] == {"changed": False, "pending_confirmation": True,
                                      "proposal_id": "nq", "proposal": quote["proposal"]}
    assert wire["capability_request"] == TRAINING_WIRE
    assert wire["training_plan"] == {"changed": False, "pending_confirmation": False,
                                     "proposal_id": None}


# ── The wire projection ──────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    None, "training_plan_write", [], {"capability": "training_plan_write"},
    {"capability": "training_plan_write", "purpose": "coach.nutrition_plan_access"},
    {"capability": "nutrition.plan", "purpose": "nutrition.plan"},
    {"capability": "training_plan_write", "purpose": "coach.training_plan_edit", "x": 1},
])
def test_projection_publishes_only_an_exact_registered_pair(value):
    projected = mobile_coach.project_plan_effects({"changed": False, "capability_request": value})
    assert "capability_request" not in projected


@pytest.mark.parametrize("pair", [TRAINING_WIRE, NUTRITION_WIRE])
def test_projection_matches_the_mobile_decoder_contract(pair):
    projected = mobile_coach.project_plan_effects({"changed": False, "capability_request": dict(pair)})
    # Mobile #235 CoachApiDto.capabilityRequest: a map of exactly two keys,
    # capability ∈ {training_plan_write, nutrition_plan_access}, purpose = its own.
    assert projected["capability_request"] == pair and len(projected["capability_request"]) == 2
    assert {(s.name, s.purpose) for s in caps.SPECS.values()} == {
        (TRAINING_WIRE["capability"], TRAINING_WIRE["purpose"]),
        (NUTRITION_WIRE["capability"], NUTRITION_WIRE["purpose"])}


# ── The native route, end to end (synthetic provider, real pipeline) ─────

from tests.test_mobile_coach_api import (  # noqa: E402,F401
    POST_PATH, alice, bearer, mobile, no_real_provider, provider,
)


def _route_turn(mobile, headers, provider, calls, message="Add one set to my bench press"):
    seen = {}

    def loop(user_id, question):
        seen["tools"] = _names(t["function"] for t in ai_coach._openai_tools_for_call(user_id))
        seen["results"] = [json.loads(ai_coach._dispatch_coach_tool(
            user_id, name, json.dumps(arguments))) for name, arguments in calls]
        return "I can't change your plan yet. You can review the permission in the app."
    provider.side_effect = loop
    response = mobile.post(POST_PATH, headers=headers, json={"message": message})
    return response, seen


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_route_publishes_the_exact_mobile_wire_and_nothing_else(
        app, mobile, bearer, provider, alice, flag_on, monkeypatch, training):
    FakeD1(monkeypatch, generation=1, **{"coach.conversation": _record(R2)})
    response, seen = _route_turn(mobile, bearer(alice), provider, [(TOOL, {"capability": TRAINING})])
    assert response.status_code == 200
    body = response.get_json()
    assert set(body) == {"contract_version", "messages", "plan_effects"}
    assert body["contract_version"] == 1
    assert body["plan_effects"] == {
        "training_plan": {"changed": False, "pending_confirmation": False, "proposal_id": None},
        "capability_request": TRAINING_WIRE}
    assert TOOL in seen["tools"] and not set(seen["tools"]) & coach_plan_tools.PLAN_WRITE_TOOL_NAMES
    assert seen["results"] == [{"status": "ok", "outcome": "review_offered"}]
    # The reply is ordinary chat: persisted once, chat is never blocked.
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_route_ordinary_r2_chat_is_byte_identical_in_this_source(
        app, mobile, bearer, provider, alice, flag_on, monkeypatch):
    """Nothing approved (all of #569): no request tool, no new key."""
    FakeD1(monkeypatch, generation=1, **{"coach.conversation": _record(R2)})
    response, seen = _route_turn(mobile, bearer(alice), provider, [], message="How much protein?")
    assert response.get_json()["plan_effects"] == {
        "training_plan": {"changed": False, "pending_confirmation": False, "proposal_id": None}}
    assert TOOL not in seen["tools"]


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_route_flag_off_wire_is_unchanged_even_with_an_open_capability(
        app, mobile, bearer, provider, alice, monkeypatch, training):
    FakeD1(monkeypatch, generation=1, **{"coach.conversation": _record(R2)})
    app.config[coach_plan_tools.FLAG_KEY] = False
    response, seen = _route_turn(mobile, bearer(alice), provider, [(TOOL, {"capability": TRAINING})])
    assert response.get_json()["plan_effects"] == {
        "training_plan": {"changed": False, "pending_confirmation": False, "proposal_id": None}}
    assert TOOL not in seen["tools"]


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_route_a_write_tool_without_the_capability_is_refused_and_no_request_is_inferred(
        app, mobile, bearer, provider, alice, flag_on, monkeypatch, training):
    """A forged write call is refused before any read or write, and a refusal
    is never turned into a request: only the closed request tool raises one."""
    FakeD1(monkeypatch, generation=1, **{"coach.conversation": _record(R2)})
    response, seen = _route_turn(mobile, bearer(alice), provider, [(
        "update_training_plan_prescription",
        {"day": "Pazartesi", "exercise": "Bench Press", "sets": 4})])
    assert seen["results"][0]["error"] == "CAPABILITY_DISABLED"
    assert "capability_request" not in response.get_json()["plan_effects"]
