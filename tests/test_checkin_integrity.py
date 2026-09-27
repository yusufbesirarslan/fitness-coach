"""One Check-in attempt has one durable result; a new attempt is separate."""

from app.blueprints import tracking
from app.models import WeeklyCheckIn


PAYLOAD = {"weight": 79, "yogunluk": 4, "fatigue": 2,
           "progressive_overload": "evet", "uyku_kalitesi": 5,
           "beslenme_uyumu": 4, "note": "good week"}


def test_same_token_replay_returns_first_result_without_second_feedback(
        client, auth_user, monkeypatch):
    calls = []

    def feedback(*args, **kwargs):
        calls.append(1)
        return "feedback once"

    monkeypatch.setattr(tracking, "generate_checkin_feedback", feedback)
    headers = {"Idempotency-Key": "checkin-attempt-0001"}
    first = client.post("/checkin", json=PAYLOAD, headers=headers)
    replay = client.post("/checkin", json=PAYLOAD, headers=headers)

    assert first.status_code == replay.status_code == 200
    assert first.get_json() == replay.get_json()
    assert WeeklyCheckIn.query.filter_by(user_id=auth_user.id).count() == 1
    assert len(calls) == 1


def test_new_token_allows_intentional_second_checkin(client, auth_user, monkeypatch):
    monkeypatch.setattr(tracking, "generate_checkin_feedback",
                        lambda *args, **kwargs: "feedback")
    for token in ("checkin-attempt-0001", "checkin-attempt-0002"):
        response = client.post("/checkin", json=PAYLOAD,
                               headers={"Idempotency-Key": token})
        assert response.status_code == 200
    assert WeeklyCheckIn.query.filter_by(user_id=auth_user.id).count() == 2


def test_invalid_token_is_rejected_before_feedback(client, auth_user, monkeypatch):
    calls = []
    monkeypatch.setattr(tracking, "generate_checkin_feedback",
                        lambda *args, **kwargs: calls.append(1))
    response = client.post("/checkin", json=PAYLOAD,
                           headers={"Idempotency-Key": "bad key"})
    assert response.status_code == 400
    assert not calls
    assert WeeklyCheckIn.query.filter_by(user_id=auth_user.id).count() == 0


def test_same_token_with_changed_payload_is_conflict(client, auth_user, monkeypatch):
    calls = []
    monkeypatch.setattr(tracking, "generate_checkin_feedback",
                        lambda *args, **kwargs: calls.append(1) or "feedback")
    headers = {"Idempotency-Key": "checkin-attempt-0001"}
    assert client.post("/checkin", json=PAYLOAD, headers=headers).status_code == 200
    changed = {**PAYLOAD, "weight": 78}
    response = client.post("/checkin", json=changed, headers=headers)
    assert response.status_code == 409
    assert WeeklyCheckIn.query.filter_by(user_id=auth_user.id).count() == 1
    assert len(calls) == 1


def test_same_key_belongs_to_each_user(client, make_user, login, monkeypatch):
    monkeypatch.setattr(tracking, "generate_checkin_feedback",
                        lambda *args, **kwargs: "feedback")
    headers = {"Idempotency-Key": "checkin-attempt-shared"}
    for name in ("checkinowner1", "checkinowner2"):
        make_user(username=name)
        assert login(name, "unused").status_code == 200
        assert client.post("/checkin", json=PAYLOAD, headers=headers).status_code == 200
    assert WeeklyCheckIn.query.filter_by(idempotency_key=headers["Idempotency-Key"]).count() == 2
