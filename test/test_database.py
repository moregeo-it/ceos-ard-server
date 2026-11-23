from sqlalchemy import text

from app.db.database import IDLE_TRANSACTION_TIMEOUT, SessionLocal


def test_sessions_end_forgotten_transactions(repositories):
    with SessionLocal() as db:
        assert db.execute(text("SHOW idle_in_transaction_session_timeout")).scalar() == IDLE_TRANSACTION_TIMEOUT
