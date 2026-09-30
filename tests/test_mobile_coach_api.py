"""Contract tests for the native Coach (LP-09).

    POST /api/v1/coach/messages
    GET  /api/v1/coach/history

The routes are a transport over the web Coach pipeline, so everything below runs
the REAL pipeline - memory window, persistence (`CoachConversation`/
`CoachMessage` in the normal test database), grounding (`context_builder`),
turn setup, moderation, quota and failure bookkeeping - and fakes exactly one
thing: the provider tool loop (`ai_coach._run_coach_conversation_openai`, with
Bedrock off). The fake records what it was handed, which is how these tests
prove the provider WOULD have been called with the server-owned history and
grounding, and a guard fixture makes every real provider entry point raise.

Ownership proofs use real opaque Bearer credentials (`mobile_auth.login`; only
Cognito is faked), two accounts, interleaved.

    python -m pytest tests/test_mobile_coach_api.py -v
"""
import calendar
import re
from datetime import datetime, timedelta

import pytest

from app.blueprints import mobile_coach as mobile_coach_bp
from app.extensions import db, limiter
from app.models import (
    CoachConversation, CoachMessage, MobileAccessCredential, User,
)
from app.services import (
    ai, ai_coach, ai_gate, ai_recovery, cognito_jwt, cognito_service,
    coach_plan_tools, memory_manager, mobile_auth, premium,
)
from app.services.mobile_coach import HISTORY_LIMIT, message_id
from app.services.moderation import MAX_QUESTION_CHARS
from app.services.response_formatter import COACH_FALLBACKS


POST_PATH = "/api/v1/coach/messages"
HISTORY_PATH = "/api/v1/coach/history"
ENVELOPE_KEYS = {"code", "message", "retryable", "request_id"}
MESSAGE_KEYS = {"id", "role", "text", "created_at", "interrupted"}
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24}$")


# -- Principals ----------------------------------------------------------------
@pytest.fixture
def bearer(monkeypatch):
    """Issue a real opaque mobile session for a user; only Cognito is faked."""
    subs = {}

    def authenticate(username, password):
        sub = subs[username]
        return {"tokens": {
            "access_token": f"access|{sub}", "id_token": f"id|{sub}",
            "refresh_token": f"refresh|{sub}", "expires_in": 3600},
            "claims": {"sub": sub}}

    def validate(token, expected_use, leeway_seconds=0):
        sub = token.split("|", 1)[1]
        if expected_use == "id":
            return {"sub": sub, "email": f"{sub}@example.com",
                    "email_verified": True}
        return {"sub": sub, "exp": calendar.timegm(
            (datetime.utcnow() + timedelta(hours=1)).timetuple())}

    monkeypatch.setattr(cognito_service, "authenticate", authenticate)
    monkeypatch.setattr(cognito_jwt, "validate_token", validate)

    def issue(user):
        subs[user.username] = user.cognito_sub
        issued = mobile_auth.login(user.username, "Sifre123")
        return {"Authorization": f"Bearer {issued.access_credential}"}
    return issue


@pytest.fixture
def alice(make_user):
    return make_user("alice", language="en", user_metadata={
        "injuries": "left knee tendinitis"})


@pytest.fixture
def bob(make_user):
    return make_user("bob", user_metadata={
        "injuries": "right shoulder impingement"})


# -- Provider boundary -----------------------------------------------------------
@pytest.fixture(autouse=True)
def no_real_provider(monkeypatch):
    """Every real model entry point fails the test if it is reached."""
    def forbidden(*args, **kwargs):
        raise AssertionError("a real model provider was called")

    monkeypatch.setattr(ai, "_openai_chat", forbidden)
    monkeypatch.setattr(ai, "_claude_chat", forbidden)
    monkeypatch.setattr(ai, "_heavy_complete", forbidden)
    monkeypatch.setattr(ai, "_heavy_chat", forbidden)
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_bedrock", forbidden)
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)


class FakeProvider:
    """Stands in for the provider tool loop and records what it was given."""

    def __init__(self):
        self.calls = []
        self.replies = []
        self.side_effect = None

    def __call__(self, user_id, question, context, history, language="tr",
                 deadline=None):
        self.calls.append({"user_id": user_id, "question": question,
                           "context": context,
                           "history": [dict(m) for m in history],
                           "language": language})
        if self.side_effect is not None:
            result = self.side_effect(user_id, question)
            if result is not None:
                return result
        if self.replies:
            return self.replies.pop(0)
        return f"reply to: {question}"


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai", fake)
    return fake


@pytest.fixture
def mobile(app):
    """A cookie-less native client (no auto-Origin, no browser session)."""
    return app.test_client()


# -- Helpers ------------------------------------------------------------------------
def send(client, headers, message="How much protein?", **kwargs):
    body = kwargs.pop("json", {"message": message})
    return client.post(POST_PATH, headers=headers, json=body, **kwargs)


def history(client, headers, path=HISTORY_PATH, **kwargs):
    return client.get(path, headers=headers, **kwargs)


def _error(response):
    body = response.get_json()
    assert set(body) == {"error"}
    assert set(body["error"]) == ENVELOPE_KEYS
    assert body["error"]["request_id"]
    return body["error"]


def _rows(user_id):
    return (CoachMessage.query.join(CoachConversation)
            .filter(CoachConversation.user_id == user_id)
            .order_by(CoachMessage.id.asc()).all())


def _chat_used(user_id):
    db.session.expire_all()
    meta = db.session.get(User, user_id).user_metadata or {}
    return int((meta.get("ai_plan_quota") or {}).get("chat", 0))


def _texts(body):
    return [(m["role"], m["text"]) for m in body["messages"]]


# -- AUTH ---------------------------------------------------------------------------
@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer "},
    {"Authorization": "Basic abc"},
    {"Authorization": "Bearer not-a-real-credential"},
])
def test_unauthenticated_post_is_rejected_before_any_work(
        mobile, provider, alice, headers):
    response = send(mobile, headers)

    assert response.status_code == 401
    assert _error(response)["code"] == "AUTH_SESSION_EXPIRED"
    assert response.headers["Cache-Control"] == "no-store"
    assert provider.calls == []
    assert _rows(alice.id) == []
    assert _chat_used(alice.id) == 0


@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer not-a-real-credential"}])
def test_unauthenticated_history_is_rejected(mobile, alice, headers):
    response = history(mobile, headers)

    assert response.status_code == 401
    assert _error(response)["code"] == "AUTH_SESSION_EXPIRED"
    assert response.headers["Cache-Control"] == "no-store"


def test_expired_and_revoked_credentials_follow_the_canonical_middleware(
        mobile, bearer, provider, alice):
    headers = bearer(alice)
    assert history(mobile, headers).status_code == 200

    MobileAccessCredential.query.update(
        {"expires_at": datetime.utcnow() - timedelta(seconds=1)})
    db.session.commit()
    for response in (send(mobile, headers), history(mobile, headers)):
        assert response.status_code == 401
        assert _error(response) == {**_error(response),
                                    "code": "AUTH_SESSION_EXPIRED",
                                    "retryable": False}

    revoked = bearer(alice)
    mobile.post("/api/v1/auth/logout", headers=revoked)
    assert send(mobile, revoked).status_code == 401
    assert provider.calls == []


def test_web_cookie_session_alone_cannot_use_the_native_routes(
        client, login, make_user, provider):
    make_user("webonly")
    assert login("webonly").status_code in (200, 302)

    assert client.get(HISTORY_PATH).status_code == 401
    assert client.post(POST_PATH, json={"message": "hi"}).status_code == 401
    assert provider.calls == []


# -- VALIDATION ------------------------------------------------------------------
@pytest.mark.parametrize(("body", "code"), [
    ({}, "COACH_INVALID_REQUEST"),
    ({"message": None}, "COACH_INVALID_REQUEST"),
    ({"message": 42}, "COACH_INVALID_REQUEST"),
    ({"message": ["hi"]}, "COACH_INVALID_REQUEST"),
    ({"message": {"text": "hi"}}, "COACH_INVALID_REQUEST"),
    (["hi"], "COACH_INVALID_REQUEST"),
    ("hi", "COACH_INVALID_REQUEST"),
    ({"message": ""}, "COACH_MESSAGE_EMPTY"),
    ({"message": "   \n\t "}, "COACH_MESSAGE_EMPTY"),
    ({"message": "x" * (MAX_QUESTION_CHARS + 1)}, "COACH_MESSAGE_TOO_LONG"),
    # The server owns ownership and the transcript: these keys are refused,
    # not silently ignored.
    ({"message": "hi", "user_id": 1}, "COACH_INVALID_REQUEST"),
    ({"message": "hi", "conversation_id": 1}, "COACH_INVALID_REQUEST"),
    ({"message": "hi", "history": []}, "COACH_INVALID_REQUEST"),
    ({"message": "hi", "handoff": "review_progress_insight"},
     "COACH_INVALID_REQUEST"),
])
def test_invalid_bodies_are_typed_400s_that_spend_nothing(
        mobile, bearer, provider, alice, body, code):
    response = send(mobile, bearer(alice), json=body)

    assert response.status_code == 400
    error = _error(response)
    assert error["code"] == code
    assert error["retryable"] is False
    assert response.headers["Cache-Control"] == "no-store"
    assert provider.calls == []
    assert _rows(alice.id) == []
    assert _chat_used(alice.id) == 0


def test_message_at_the_product_limit_is_accepted_after_trimming(
        mobile, bearer, provider, alice):
    text = "y" * MAX_QUESTION_CHARS
    response = send(mobile, bearer(alice), message=f"  {text}  ")

    assert response.status_code == 200
    assert provider.calls[0]["question"] == text


@pytest.mark.parametrize("kwargs", [
    {"data": "message=hi",
     "content_type": "application/x-www-form-urlencoded"},
    {"data": '{"message": "hi"}', "content_type": "text/plain"},
    {"data": "{not json", "content_type": "application/json"},
])
def test_non_json_bodies_are_rejected_in_the_mobile_envelope(
        mobile, bearer, provider, alice, kwargs):
    response = mobile.post(POST_PATH, headers=bearer(alice), **kwargs)

    assert response.status_code == 400
    assert response.is_json
    assert _error(response)["code"] == "COACH_INVALID_REQUEST"
    assert provider.calls == []


# -- POST SUCCESS / PERSISTENCE / PROVIDER CONTEXT ------------------------------
def test_success_returns_this_turn_and_persists_it_once(
        app, mobile, bearer, provider, alice):
    response = send(mobile, bearer(alice), message="  How much protein?  ")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "Set-Cookie" not in response.headers
    body = response.get_json()
    assert set(body) == {"contract_version", "messages"}
    assert body["contract_version"] == 1

    rows = _rows(alice.id)
    assert [(r.role, r.content) for r in rows] == [
        ("user", "How much protein?"),
        ("assistant", "reply to: How much protein?")]
    secret = app.config["SECRET_KEY"]
    assert body["messages"] == [
        {"id": message_id(secret, alice.id, rows[0].id), "role": "user",
         "text": "How much protein?",
         "created_at": rows[0].created_at.isoformat() + "Z",
         "interrupted": False},
        {"id": message_id(secret, alice.id, rows[1].id), "role": "assistant",
         "text": "reply to: How much protein?",
         "created_at": rows[1].created_at.isoformat() + "Z",
         "interrupted": False},
    ]
    # One shared weekly allowance was spent, in the SAME bucket web /ask uses.
    assert _chat_used(alice.id) == 1


def test_provider_receives_server_owned_owner_language_and_grounding(
        mobile, bearer, provider, alice, bob):
    assert send(mobile, bearer(alice), message="Can I squat?").status_code == 200

    (call,) = provider.calls
    assert call["user_id"] == alice.id
    assert call["question"] == "Can I squat?"
    assert call["language"] == "en"
    assert call["history"] == []
    # The canonical context_builder ran for the Bearer owner, and only for them.
    assert "left knee tendinitis" in call["context"]
    assert "right shoulder impingement" not in call["context"]


def test_second_message_sees_the_persisted_history_not_a_client_transcript(
        mobile, bearer, provider, alice):
    headers = bearer(alice)
    provider.replies = ["Aim for 1.6 g/kg.", "Yes, spread it over meals."]
    assert send(mobile, headers, message="How much protein?").status_code == 200
    assert send(mobile, headers, message="Split it?").status_code == 200

    assert provider.calls[0]["history"] == []
    assert provider.calls[1]["history"] == [
        {"role": "user", "content": "How much protein?"},
        {"role": "assistant", "content": "Aim for 1.6 g/kg."},
    ]

    body = history(mobile, headers).get_json()
    assert _texts(body) == [
        ("user", "How much protein?"), ("assistant", "Aim for 1.6 g/kg."),
        ("user", "Split it?"), ("assistant", "Yes, spread it over meals.")]


def test_response_is_this_requests_turn_even_if_another_turn_lands_meanwhile(
        mobile, bearer, provider, alice):
    """A concurrent same-user turn committing mid-generation must not be
    returned as this request's answer (the rows come from record_turn, not a
    'last two messages' re-read)."""
    def concurrent_turn(user_id, question):
        conversation = memory_manager.get_or_create_active_conversation(user_id)
        memory_manager.record_turn(conversation, "other tab", "other answer")
        return None

    provider.side_effect = concurrent_turn
    response = send(mobile, bearer(alice), message="mine")

    assert _texts(response.get_json()) == [
        ("user", "mine"), ("assistant", "reply to: mine")]
    assert [r.content for r in _rows(alice.id)] == [
        "other tab", "other answer", "mine", "reply to: mine"]


def test_unpersisted_turn_is_returned_without_identity(
        app, mobile, bearer, provider, alice):
    app.config["AI_MEMORY_ENABLED"] = False
    response = send(mobile, bearer(alice), message="hi")

    assert response.status_code == 200
    assert response.get_json()["messages"] == [
        {"id": None, "role": "user", "text": "hi", "created_at": None,
         "interrupted": False},
        {"id": None, "role": "assistant", "text": "reply to: hi",
         "created_at": None, "interrupted": False}]
    assert _rows(alice.id) == []
    assert "Set-Cookie" not in response.headers


def test_domain_cookie_session_writes_never_reach_a_native_response(
        mobile, bearer, provider, alice):
    from flask import session

    def mirror_state(user_id, question):
        session["_coach_plan_clarification"] = {"user_id": user_id}
        return None

    provider.side_effect = mirror_state
    response = send(mobile, bearer(alice))

    assert response.status_code == 200
    assert "Set-Cookie" not in response.headers


# -- HISTORY -------------------------------------------------------------------
def test_history_is_bounded_deterministic_and_oldest_first(
        mobile, bearer, alice):
    conversation = memory_manager.get_or_create_active_conversation(alice.id)
    same_instant = datetime(2026, 9, 1, 12, 0, 0)
    for n in range(HISTORY_LIMIT // 2 + 5):             # 60 messages
        memory_manager.record_turn(conversation, f"q{n}", f"a{n}")
    CoachMessage.query.update({"created_at": same_instant})  # ties -> id order
    db.session.commit()

    headers = bearer(alice)
    first = history(mobile, headers)
    second = history(mobile, headers)

    assert first.status_code == 200
    assert first.headers["Cache-Control"] == "no-store"
    body = first.get_json()
    assert body == second.get_json()
    assert set(body) == {"contract_version", "messages"}
    assert len(body["messages"]) == HISTORY_LIMIT == 50
    expected = []
    for n in range(5, HISTORY_LIMIT // 2 + 5):
        expected += [("user", f"q{n}"), ("assistant", f"a{n}")]
    assert _texts(body) == expected


def test_history_exposes_no_hidden_or_internal_fields(
        app, mobile, bearer, alice):
    conversation = memory_manager.get_or_create_active_conversation(alice.id)
    memory_manager.record_turn(conversation, "q", "a",
                               usage={"prompt_tokens": 91,
                                      "completion_tokens": 7})
    memory_manager.record_turn(conversation, "stopped", "partial",
                               interrupted=True)
    conversation.summary = "SECRET MODEL SUMMARY"
    db.session.commit()

    response = history(mobile, bearer(alice))
    body = response.get_json()
    raw = response.get_data(as_text=True)

    for message in body["messages"]:
        assert set(message) == MESSAGE_KEYS
        assert TOKEN_RE.fullmatch(message["id"])
    assert body["messages"][-1]["interrupted"] is True
    assert "SECRET MODEL SUMMARY" not in raw
    ids = {str(r.id) for r in _rows(alice.id)} | {str(conversation.id)}
    for message in body["messages"]:
        assert message["id"] not in ids
    for forbidden in ("conversation_id", "user_id", "prompt_tokens",
                      "completion_tokens", "token_estimate", "summary",
                      alice.cognito_sub, '"91"', ":91"):
        assert forbidden not in raw


def test_archived_conversation_is_not_the_active_history(
        mobile, bearer, provider, alice):
    headers = bearer(alice)
    send(mobile, headers, message="old")
    memory_manager.archive_active_conversation(alice.id)

    assert history(mobile, headers).get_json()["messages"] == []


def test_history_matches_the_web_history_for_the_same_owner(
        client, login, mobile, bearer, provider, make_user):
    carol = make_user("carol")
    headers = bearer(carol)
    conversation = memory_manager.get_or_create_active_conversation(carol.id)
    for n in range(HISTORY_LIMIT // 2 + 3):      # past the window on purpose
        memory_manager.record_turn(conversation, f"q{n}", f"a{n}")
    send(mobile, headers, message="one")
    send(mobile, headers, message="two")
    assert login("carol").status_code in (200, 302)

    web = client.get("/coach/history").get_json()
    native = history(mobile, headers).get_json()

    assert len(native["messages"]) == HISTORY_LIMIT
    assert [(m["role"], m["text"]) for m in web["messages"]] == _texts(native)
    assert _texts(native)[-2:] == [("user", "two"), ("assistant", "reply to: two")]
    assert [m["created_at"] for m in web["messages"]] == [
        m["created_at"] for m in native["messages"]]


def test_history_read_failure_is_a_coach_503_not_an_auth_error(
        mobile, bearer, alice, monkeypatch):
    headers = bearer(alice)

    def boom(*args, **kwargs):
        raise RuntimeError("SELECT secret FROM coach_message")

    monkeypatch.setattr(memory_manager, "recent_messages", boom)
    response = history(mobile, headers)

    assert response.status_code == 503
    error = _error(response)
    assert error["code"] == "COACH_HISTORY_UNAVAILABLE"
    assert error["retryable"] is True
    assert "SELECT" not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"


# -- ISOLATION -----------------------------------------------------------------
def test_two_accounts_are_isolated_in_both_directions(
        mobile, bearer, provider, alice, bob):
    a, b = bearer(alice), bearer(bob)
    provider.replies = ["A1", "B1", "A2"]

    assert send(mobile, a, message="alice one").status_code == 200
    assert history(mobile, b).get_json()["messages"] == []

    assert send(mobile, b, message="bob one").status_code == 200
    # B's generation saw none of A's turns and none of A's grounding.
    assert provider.calls[1]["history"] == []
    assert provider.calls[1]["user_id"] == bob.id
    assert "left knee tendinitis" not in provider.calls[1]["context"]

    a_before = history(mobile, a).get_json()
    assert _texts(a_before) == [("user", "alice one"), ("assistant", "A1")]
    assert _texts(history(mobile, b).get_json()) == [
        ("user", "bob one"), ("assistant", "B1")]

    assert send(mobile, a, message="alice two").status_code == 200
    assert provider.calls[2]["history"] == [
        {"role": "user", "content": "alice one"},
        {"role": "assistant", "content": "A1"}]
    assert _texts(history(mobile, b).get_json()) == [
        ("user", "bob one"), ("assistant", "B1")]


def test_client_supplied_identifiers_cannot_select_another_conversation(
        mobile, bearer, provider, alice, bob):
    a, b = bearer(alice), bearer(bob)
    send(mobile, a, message="alice private")
    alice_conversation = CoachConversation.query.filter_by(
        user_id=alice.id).one()
    alice_ids = [m["id"] for m in history(mobile, a).get_json()["messages"]]

    tampered = [
        f"{HISTORY_PATH}?user_id={alice.id}",
        f"{HISTORY_PATH}?conversation_id={alice_conversation.id}",
        f"{HISTORY_PATH}?user={alice.username}&limit=500",
        f"{HISTORY_PATH}?id={alice_ids[0]}",
    ]
    for path in tampered:
        response = history(mobile, {**b, "X-User-Id": str(alice.id)},
                           path=path)
        assert response.status_code == 200
        assert response.get_json()["messages"] == []

    response = send(mobile, b, json={"message": "hi", "user_id": alice.id})
    assert response.status_code == 400
    assert len(_rows(alice.id)) == 2


def test_message_ids_are_bound_to_their_owner(app):
    secret = app.config["SECRET_KEY"]
    assert message_id(secret, 1, 7) != message_id(secret, 2, 7)
    assert message_id(secret, 1, 7) == message_id(secret, 1, 7)
    assert message_id(secret, 1, 7) != message_id("other-secret", 1, 7)
    assert TOKEN_RE.fullmatch(message_id(secret, 1, 7))


# -- QUOTA / PRODUCT ERRORS -------------------------------------------------------
def _exhaust_weekly_chats(user):
    user.user_metadata = {"ai_plan_quota": {
        "week": premium._week_key(), "chat": premium.FREE_WEEKLY_AI_CHATS}}
    db.session.commit()


def test_402_when_the_shared_weekly_allowance_is_used_up(
        app, mobile, bearer, provider, alice):
    app.config["AI_CHAT_QUOTA_ENABLED"] = True
    _exhaust_weekly_chats(alice)

    response = send(mobile, bearer(alice))

    assert response.status_code == 402
    error = _error(response)
    assert error["code"] == "COACH_QUOTA_EXCEEDED"
    assert error["retryable"] is False
    assert "Retry-After" not in response.headers
    assert response.headers["Cache-Control"] == "no-store"
    assert provider.calls == []
    assert _rows(alice.id) == []
    assert _chat_used(alice.id) == premium.FREE_WEEKLY_AI_CHATS


def test_premium_owner_is_never_quota_limited(
        app, mobile, bearer, provider, make_user):
    app.config["AI_CHAT_QUOTA_ENABLED"] = True
    vip = make_user("vip", is_premium=True)
    _exhaust_weekly_chats(vip)

    assert send(mobile, bearer(vip)).status_code == 200


def test_429_cooldown_short_circuits_before_quota_and_provider(
        mobile, bearer, provider, alice, monkeypatch):
    monkeypatch.setattr(ai_recovery, "ai_cooldown_remaining", lambda uid: 42)

    response = send(mobile, bearer(alice))

    assert response.status_code == 429
    error = _error(response)
    assert error["code"] == "COACH_COOLING_DOWN"
    assert error["retryable"] is True
    assert response.headers["Retry-After"] == "42"
    assert provider.calls == []
    assert _chat_used(alice.id) == 0


@pytest.fixture
def enabled_limiter():
    limiter.reset()
    limiter.enabled = True
    try:
        yield
    finally:
        limiter.enabled = False
        limiter.reset()


def test_429_burst_limit_is_typed_retryable_and_skips_the_provider(
        mobile, bearer, provider, alice, bob, enabled_limiter, monkeypatch):
    monkeypatch.setattr(mobile_coach_bp, "AI_BURST_RATELIMIT", "2 per minute")
    a = bearer(alice)

    codes = [send(mobile, a).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    response = send(mobile, a)
    error = _error(response)
    assert error["code"] == "COACH_RATE_LIMITED"
    assert error["retryable"] is True
    assert int(response.headers["Retry-After"]) > 0
    assert len(provider.calls) == 2
    assert _chat_used(alice.id) == 2
    # Keyed on the Bearer owner: B is not throttled by A's burst.
    assert send(mobile, bearer(bob)).status_code == 200


def test_503_when_the_ai_concurrency_gate_is_full(
        mobile, bearer, provider, alice, monkeypatch):
    class Full:
        def acquire(self, timeout=None):
            return False

    headers = bearer(alice)  # login itself takes a blocking slot
    monkeypatch.setattr(ai_gate, "_ai_slots", Full())
    response = send(mobile, headers)

    assert response.status_code == 503
    error = _error(response)
    assert error["code"] == "COACH_BUSY"
    assert error["retryable"] is True
    assert response.headers["Retry-After"] == "15"
    assert provider.calls == []
    assert _chat_used(alice.id) == 0


@pytest.mark.parametrize("language", ["tr", "en"])
def test_503_provider_fallback_is_never_a_fake_reply(
        app, mobile, bearer, provider, make_user, monkeypatch, language):
    app.config["AI_CHAT_QUOTA_ENABLED"] = True
    user = make_user(f"fb-{language}", language=language)
    failures = []
    monkeypatch.setattr(ai_recovery, "record_ai_failure", failures.append)
    fallback = COACH_FALLBACKS[language]["error"]
    provider.replies = [fallback]

    response = send(mobile, bearer(user))

    assert response.status_code == 503
    error = _error(response)
    assert error["code"] == "COACH_UNAVAILABLE"
    assert error["retryable"] is True
    assert fallback not in response.get_data(as_text=True)
    assert "messages" not in response.get_json()
    assert len(provider.calls) == 1
    assert _rows(user.id) == []                 # B16: never persisted
    assert _chat_used(user.id) == 0             # refunded
    assert failures == [user.id]                # failure streak advanced


def test_503_provider_exception_leaks_nothing(
        app, mobile, bearer, provider, alice, monkeypatch):
    app.config["AI_CHAT_QUOTA_ENABLED"] = True
    failures = []
    monkeypatch.setattr(ai_recovery, "record_ai_failure", failures.append)

    def explode(user_id, question):
        raise RuntimeError("provider said: sk-live-SECRET prompt=SYSTEM")

    provider.side_effect = explode
    response = send(mobile, bearer(alice))

    assert response.status_code == 503
    assert _error(response)["code"] == "COACH_UNAVAILABLE"
    raw = response.get_data(as_text=True)
    for leaked in ("sk-live", "SYSTEM", "RuntimeError", "Traceback"):
        assert leaked not in raw
    assert _rows(alice.id) == []
    assert _chat_used(alice.id) == 0
    assert failures == [alice.id]


@pytest.mark.parametrize("flag", [
    "plan_changed_this_turn", "proposal_created_this_turn"])
def test_503_after_a_committed_plan_side_effect_is_not_retryable(
        mobile, bearer, provider, alice, monkeypatch, flag):
    monkeypatch.setattr(coach_plan_tools, flag, lambda: True)
    provider.replies = [COACH_FALLBACKS["en"]["tool_plan_saved"]]

    response = send(mobile, bearer(alice))

    assert response.status_code == 503
    error = _error(response)
    assert error["code"] == "COACH_REPLY_INCOMPLETE"
    assert error["retryable"] is False
    assert _rows(alice.id) == []


def test_success_clears_the_failure_streak(
        mobile, bearer, provider, alice, monkeypatch):
    cleared = []
    monkeypatch.setattr(ai_recovery, "clear_ai_failures", cleared.append)

    assert send(mobile, bearer(alice)).status_code == 200
    assert cleared == [alice.id]


# -- ROUTING ------------------------------------------------------------------------
def test_exact_paths_methods_and_auth_marker(app):
    rules = {(r.rule, tuple(sorted(r.methods - {"HEAD", "OPTIONS"})))
             for r in app.url_map.iter_rules()
             if r.endpoint in ("mobile_api.send_coach_message",
                               "mobile_api.coach_history")}
    assert rules == {("/api/v1/coach/messages", ("POST",)),
                     ("/api/v1/coach/history", ("GET",))}
    for endpoint in ("mobile_api.send_coach_message", "mobile_api.coach_history"):
        assert getattr(app.view_functions[endpoint], "_require_mobile_auth",
                       False) is True


@pytest.mark.parametrize(("method", "path", "status"), [
    ("get", "/api/v1/coach/messages", 405),
    ("post", "/api/v1/coach/history", 405),
    ("get", "/api/v1/coach", 404),
    ("get", "/api/v1/coach/history/abc", 404),
    ("get", "/api/v1/coach/messages/abc", 404),
    ("post", "/api/v1/coach/conversation/reset", 404),
    ("post", "/api/v1/ask", 404),
    ("post", "/api/v1/ask/stream", 404),
    ("post", "/api/v1/chat", 404),
    ("post", "/coach/messages", 404),
    ("get", "/v1/coach/history", 404),
])
def test_unsupported_sibling_paths_stay_unavailable(
        mobile, bearer, provider, alice, method, path, status):
    response = getattr(mobile, method)(path, headers=bearer(alice),
                                       json={"message": "hi"})
    assert response.status_code == status
    assert provider.calls == []


def test_web_coach_routes_do_not_accept_a_bearer_credential(
        mobile, bearer, provider, alice):
    headers = bearer(alice)
    for method, path in (("post", "/ask"), ("post", "/ask/stream"),
                         ("get", "/coach/history")):
        response = getattr(mobile, method)(
            path, headers={**headers, "Origin": "http://localhost"},
            json={"question": "hi"})
        assert response.status_code in (302, 401, 403)
    assert provider.calls == []
