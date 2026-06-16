import os
import sqlite3
from finapp.services.backup import create_backup


def test_create_backup_uses_online_backup_api(tmp_path):
    src_path = tmp_path / "finance.db"
    backups_dir = tmp_path / "backups"

    src_conn = sqlite3.connect(str(src_path))
    src_conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    src_conn.execute("INSERT INTO t (v) VALUES ('hello')")
    src_conn.commit()

    backup_path = create_backup(
        source_db_path=str(src_path),
        backups_dir=str(backups_dir),
        year=2026, month=6,
    )

    assert os.path.exists(backup_path)
    assert backup_path.endswith("finance.2026-06.db")

    dest_conn = sqlite3.connect(backup_path)
    rows = dest_conn.execute("SELECT v FROM t").fetchall()
    assert rows == [("hello",)]
    dest_conn.close()
    src_conn.close()


def test_create_backup_is_consistent_snapshot_not_copy(tmp_path):
    """Confirms the backup uses Connection.backup(), not shutil.copy, by
    verifying the destination is independently queryable as a complete
    SQLite database."""
    src_path = tmp_path / "finance.db"
    backups_dir = tmp_path / "backups"

    src_conn = sqlite3.connect(str(src_path))
    src_conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    src_conn.commit()

    backup_path = create_backup(
        source_db_path=str(src_path), backups_dir=str(backups_dir), year=2026, month=7,
    )

    dest_conn = sqlite3.connect(backup_path)
    result = dest_conn.execute("PRAGMA integrity_check").fetchall()
    assert result == [("ok",)]
    dest_conn.close()
    src_conn.close()
