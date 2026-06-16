"""
Backup service: writes a consistent point-in-time snapshot of the SQLite
database using the SQLite Online Backup API (sqlite3.Connection.backup()).
NOT shutil.copy — a raw file copy can capture a database mid-write (especially
under WAL mode) and produce a corrupt snapshot; Connection.backup() guarantees
a complete, consistent copy.
"""
import os
import sqlite3


def create_backup(source_db_path: str, backups_dir: str, year: int, month: int) -> str:
    """
    Back up source_db_path to backups_dir/finance.YYYY-MM.db using the
    SQLite Online Backup API. Creates backups_dir if it doesn't exist.
    Returns the backup file path.
    """
    os.makedirs(backups_dir, exist_ok=True)

    filename = f"finance.{year:04d}-{month:02d}.db"
    dest_path = os.path.join(backups_dir, filename)

    source_conn = sqlite3.connect(source_db_path)
    dest_conn = sqlite3.connect(dest_path)
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()

    return dest_path
