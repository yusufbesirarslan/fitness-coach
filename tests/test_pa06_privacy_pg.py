"""PA06 provenance transaction checks on an explicitly disposable PostgreSQL DB."""
import os
import threading

import pytest

pytestmark = pytest.mark.pg_concurrency
if os.environ.get('FITX_PG_CONCURRENCY_TEST') != '1':
    pytest.skip('requires opt-in disposable PostgreSQL', allow_module_level=True)


@pytest.fixture
def pg_app():
    from flask import Flask
    from app.extensions import db
    url = os.environ.get('PG_TEST_DATABASE_URL', '')
    if not url.startswith(('postgresql://', 'postgresql+psycopg2://')):
        pytest.fail('PG_TEST_DATABASE_URL must explicitly name a disposable PostgreSQL database')
    app = Flask('pa06-disposable')
    app.config.update(TESTING=True, SECRET_KEY='pa06-synthetic-signing-key',
                      SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    with app.app_context():
        db.create_all()
    try:
        yield app
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def _owner(name):
    from app.extensions import db
    from app.models import User
    user = User(username=name, email=f'{name}@example.invalid',
                user_metadata={'preferences': {'preserved': 'LOCAL_ONLY'}})
    db.session.add(user)
    db.session.commit()
    return user


def test_pg_legacy_quarantine_and_metadata_preservation(pg_app):
    from app.extensions import db
    from app.models import CoachMessage
    from app.services import memory_manager as memory
    with pg_app.app_context():
        owner = _owner('pg_pa06_owner')
        conversation = memory.get_or_create_active_conversation(owner.id)
        conversation.summary = 'FORBIDDEN_LEGACY_SOCIAL'
        db.session.add(CoachMessage(conversation_id=conversation.id, role='assistant',
                                    content='FORBIDDEN_LEGACY_SOCIAL'))
        db.session.commit()
        memory.record_turn(conversation, 'OWN_TRAINING', 'OWN_REPLY')
        assert [m['content'] for m in memory.build_context_window(conversation)] == ['OWN_TRAINING', 'OWN_REPLY']
        assert owner.user_metadata['preferences'] == {'preserved': 'LOCAL_ONLY'}
        assert len(memory.recent_messages(owner.id)[1]) == 3


def test_pg_parallel_turns_retain_provenance_for_every_committed_pair(pg_app):
    from app.extensions import db
    from app.models import CoachConversation
    from app.services import memory_manager as memory
    from app.services.coach_memory_provenance import trusted_rows
    with pg_app.app_context():
        owner = _owner('pg_pa06_parallel')
        conv_id = memory.get_or_create_active_conversation(owner.id).id
    barrier = threading.Barrier(4)
    errors = []
    def record(index):
        try:
            with pg_app.app_context():
                conv = db.session.get(CoachConversation, conv_id)
                barrier.wait(timeout=10)
                memory.record_turn(conv, f'OWN_FACT_{index}', f'OWN_REPLY_{index}')
                db.session.remove()
        except BaseException as error:
            errors.append(error)
    threads = [threading.Thread(target=record, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    with pg_app.app_context():
        conv = db.session.get(CoachConversation, conv_id)
        rows = conv.messages.all()
        assert len(rows) == len(trusted_rows(conv, rows)) == 8


def test_pg_cross_owner_signatures_and_modified_content_fail_closed(pg_app):
    from app.extensions import db
    from app.services import memory_manager as memory
    from app.services.coach_memory_provenance import signed_summary
    with pg_app.app_context():
        a, b = _owner('pg_pa06_a'), _owner('pg_pa06_b')
        ca, cb = memory.get_or_create_active_conversation(a.id), memory.get_or_create_active_conversation(b.id)
        rows = memory.record_turn(ca, 'OWN_FACT', 'OWN_REPLY')
        ca.summary = signed_summary(ca, 'OWN_SUMMARY', 0)
        cb.summary = ca.summary
        b.user_metadata = a.user_metadata
        rows[0].content = 'FORBIDDEN_MODIFIED_SOCIAL'
        db.session.commit()
        assert memory.build_context_window(cb) == []
        assert all('FORBIDDEN' not in row['content'] for row in memory.build_context_window(ca))
