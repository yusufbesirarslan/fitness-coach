"""Locked mutation boundary for the canonical mobile nutrition ledger."""
from types import SimpleNamespace

from app.extensions import db
from app.models import MealLog
from app.services.mobile_nutrition.identity import matches_diary_entry_id
from app.services.mobile_nutrition.revision import (
    DiaryEntryRevisionState,
    diary_entry_revision,
    matches_diary_entry_revision,
)
from app.services.mobile_nutrition.serialization import logged_meal

from .commands import SLOT_LABELS, SetSlotCommand


class EntryNotFound(Exception):
    pass


class StaleDiaryEntry(Exception):
    pass


def _state(row):
    return DiaryEntryRevisionState(
        user_id=row.user_id,
        entry_id=row.id,
        meal_label=row.ogun,
        description=row.yemekler,
        energy_kcal=row.kalori,
        protein_g=row.protein,
        carbohydrate_g=row.karb,
        fat_g=row.yag,
        diary_date=row.tarih,
        source=row.source,
        idempotency_key=row.idempotency_key,
        idempotency_fingerprint=row.idempotency_fingerprint,
        photo_key=row.photo_key,
        created_at=row.created_at,
    )


def _canonical_meal(row, secret, user_id):
    projected = SimpleNamespace(
        user_id=row.user_id,
        entry_id=row.id,
        meal_label=row.ogun,
        description=row.yemekler,
        source=row.source,
        created_at=row.created_at,
        energy_kcal=row.kalori,
        protein_g=row.protein,
        carbohydrate_g=row.karb,
        fat_g=row.yag,
        diary_date=row.tarih,
        idempotency_key=row.idempotency_key,
        idempotency_fingerprint=row.idempotency_fingerprint,
        photo_key=row.photo_key,
    )
    from app.services.mobile_nutrition.identity import diary_entry_id
    return logged_meal(
        projected,
        lambda entry_id: diary_entry_id(secret, user_id, entry_id),
        lambda entry: diary_entry_revision(secret, _state(row)),
    )


def _resolve_entry_id(user_id, diary_date, entry_token, secret):
    candidates = (db.session.query(MealLog.id)
                  .filter_by(user_id=user_id, tarih=diary_date)
                  .all())
    resolved = None
    for (entry_id,) in candidates:
        if matches_diary_entry_id(secret, user_id, entry_id, entry_token):
            resolved = entry_id
    if resolved is None:
        raise EntryNotFound
    return resolved


def _locked_current_row(user_id, diary_date, entry_token, revision, secret):
    entry_id = _resolve_entry_id(user_id, diary_date, entry_token, secret)
    row = (MealLog.query
           .filter_by(id=entry_id, user_id=user_id, tarih=diary_date)
           .with_for_update()
           .one_or_none())
    if row is None:
        raise EntryNotFound
    if not matches_diary_entry_revision(secret, _state(row), revision):
        raise StaleDiaryEntry
    return row


def set_slot(user_id, diary_date, entry_token, revision, command, secret):
    if not isinstance(command, SetSlotCommand):
        raise TypeError("command must be SetSlotCommand")
    row = _locked_current_row(
        user_id, diary_date, entry_token, revision, secret)
    row.ogun = SLOT_LABELS[command.slot]
    db.session.flush()
    meal = _canonical_meal(row, secret, user_id)
    db.session.commit()
    return meal


def delete_entry(user_id, diary_date, entry_token, revision, secret):
    row = _locked_current_row(
        user_id, diary_date, entry_token, revision, secret)
    db.session.delete(row)
    db.session.commit()
