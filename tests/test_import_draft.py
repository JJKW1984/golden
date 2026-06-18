"""Unit tests for the server-backed CSV import draft model, normalization,
persistence, and confirm logic."""
from datetime import date, datetime, timedelta
import pytest

from finapp.deps import AccountContext
from finapp.models import ImportDraft
from finapp.services.seeds import seed_default_categories


@pytest.fixture
def import_ctx(db, account):
    seed_default_categories(db, account.id)
    return AccountContext(account_id=account.id, db=db)


def test_import_draft_table_round_trips(import_ctx):
    draft = ImportDraft(
        account_id=import_ctx.account_id,
        created_at=datetime(2026, 6, 18, 12, 0, 0),
        expires_at=datetime(2026, 6, 18, 13, 0, 0),
        column_map_json='{"date": "Date"}',
        spent_is_negative=True,
        rows_json="[]",
        version=1,
    )
    import_ctx.db.add(draft)
    import_ctx.db.commit()

    loaded = import_ctx.db.query(ImportDraft).filter_by(
        account_id=import_ctx.account_id
    ).first()
    assert loaded is not None
    assert loaded.column_map_json == '{"date": "Date"}'
    assert loaded.spent_is_negative is True
    assert loaded.rows_json == "[]"
    assert loaded.version == 1
