"""PA06 provenance for reuse of new Coach turns, without a schema migration.

Existing unmarked prose is quarantined in place. HMACs bind an exact message to
its database owner, conversation, id and role. The private bounded index lives
in the existing metadata column; lost/concurrent metadata updates fail closed.
Summary envelopes are signed independently and never rendered as client history.
"""
import hashlib
import hmac
import json
from flask import current_app
from app.extensions import db
from app.models import User

INDEX_KEY = '_pa06_coach_turns_v1'
INDEX_LIMIT = 200


def _signature(kind, conversation, row_id, role, text):
    secret = current_app.config['SECRET_KEY']
    if not secret:
        raise RuntimeError('Coach provenance requires application signing key')
    if isinstance(secret, str):
        secret = secret.encode()
    data = json.dumps(['axisai/pa06/coach-memory/v1', kind, conversation.user_id, conversation.id, row_id, role, text],
                      ensure_ascii=False, separators=(',', ':')).encode()
    return hmac.new(secret, data, hashlib.sha256).hexdigest()


def _index(conversation):
    user = db.session.get(User, conversation.user_id)
    meta = user.user_metadata if user and isinstance(user.user_metadata, dict) else {}
    index = meta.get(INDEX_KEY)
    return dict(index) if isinstance(index, dict) else {}


def mark_turn(conversation, rows):
    """Called only by record_turn after IDs are allocated, in its transaction."""
    user = db.session.execute(db.select(User).where(User.id == conversation.user_id)
        .with_for_update().execution_options(populate_existing=True)).scalar_one()
    index = _index(conversation)
    for row in rows:
        index[str(row.id)] = _signature('turn', conversation, row.id, row.role, row.content)
    index = {key: index[key] for key in sorted(
        (key for key in index if isinstance(key, str) and key.isascii() and key.isdigit() and len(key) <= 20),
        key=int)[-INDEX_LIMIT:]}
    meta = dict(user.user_metadata or {})
    meta[INDEX_KEY] = index
    user.user_metadata = meta


def candidate_ids(conversation):
    return [int(key) for key in _index(conversation)
            if isinstance(key, str) and key.isascii() and key.isdigit() and len(key) <= 20]


def trusted_rows(conversation, rows):
    index = _index(conversation)
    return [row for row in rows if isinstance(index.get(str(row.id)), str) and index[str(row.id)].isascii()
            and hmac.compare_digest(index[str(row.id)], _signature(
                'turn', conversation, row.id, row.role, row.content))]


def summary_text(conversation):
    try:
        envelope = json.loads(conversation.summary or '')
        text, signature = envelope['text'], envelope['signature']
        if (envelope['version'] == 1 and isinstance(text, str) and isinstance(signature, str)
                and hmac.compare_digest(signature, _signature(
                    'summary', conversation, conversation.summarized_upto_id, 'summary', text))):
            return text
    except (ValueError, TypeError, KeyError):
        pass
    return ''


def signed_summary(conversation, text, upto_id):
    return json.dumps({'version': 1, 'text': text, 'signature': _signature(
        'summary', conversation, upto_id, 'summary', text)}, ensure_ascii=False)
