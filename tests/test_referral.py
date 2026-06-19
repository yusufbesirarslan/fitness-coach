"""Davet (referral) yardımcı testleri (app/services/referral.py).

Kod üretimi (alfabe + çakışma kaçınımı), yeni kayıt-davet bağı + çift-taraflı XP,
güvenli no-op'lar (kendine davet, geçersiz/eksik kod, zaten bağlı) ve eksik kodlu
kullanıcıların boot backfill'i sabitlenir.

    python -m pytest tests/test_referral.py -v
"""
from app.extensions import db
from app.models import User
from app.services.referral import (
    REFERRAL_REWARD_XP,
    backfill_referral_codes,
    consume_referral,
    ensure_referral_code,
    generate_referral_code,
)


def test_generate_code_uses_safe_alphabet(app):
    code = generate_referral_code()
    assert len(code) == 16
    assert all(c not in "01OIL" for c in code)  # kafa karıştıran karakterler yok


def test_ensure_referral_code_is_idempotent(app, make_user):
    user = make_user("kodlu")
    first = ensure_referral_code(user)
    assert first
    assert ensure_referral_code(user) == first  # ikinci çağrı aynı kodu korur


def test_consume_referral_links_and_awards_both(app, make_user):
    referrer = make_user("davetci", rank_points=0)
    ensure_referral_code(referrer)
    db.session.commit()
    invited = make_user("davetli", rank_points=0)

    result = consume_referral(invited, referrer.referral_code)
    assert result is not None and result.id == referrer.id

    db.session.expire_all()
    assert db.session.get(User, invited.id).referred_by_id == referrer.id
    assert db.session.get(User, referrer.id).rank_points == REFERRAL_REWARD_XP
    assert db.session.get(User, invited.id).rank_points == REFERRAL_REWARD_XP


def test_consume_referral_noop_cases(app, make_user):
    referrer = make_user("ref2")
    ensure_referral_code(referrer)
    db.session.commit()
    invited = make_user("inv2")

    assert consume_referral(invited, "") is None              # kod yok
    assert consume_referral(invited, "GECERSIZKOD12345") is None  # bilinmeyen kod
    assert consume_referral(referrer, referrer.referral_code) is None  # kendine davet


def test_consume_referral_case_insensitive_and_single_use(app, make_user):
    referrer = make_user("ref3")
    ensure_referral_code(referrer)
    db.session.commit()
    invited = make_user("inv3")

    # Kod küçük harfle gelse de eşleşir (strip().upper()).
    assert consume_referral(invited, referrer.referral_code.lower()) is not None
    # Zaten bağlı kullanıcı ikinci kez tüketemez.
    assert consume_referral(invited, referrer.referral_code) is None


def test_backfill_assigns_codes_to_missing_only(app, make_users_bulk):
    users = make_users_bulk(3, prefix="bf")
    users[0].referral_code = None
    users[1].referral_code = ""
    users[2].referral_code = "VARZATEN12345678"
    db.session.commit()

    count = backfill_referral_codes()
    assert count == 2  # yalnızca eksik/boş olanlara atandı

    db.session.expire_all()
    assert db.session.get(User, users[0].id).referral_code
    assert db.session.get(User, users[1].id).referral_code
    assert db.session.get(User, users[2].id).referral_code == "VARZATEN12345678"
    assert backfill_referral_codes() == 0  # ikinci çağrı: eksik yok → 0
