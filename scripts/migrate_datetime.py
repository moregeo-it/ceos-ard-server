import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Berlin")
UTC = ZoneInfo("UTC")

TABLES_AND_COLUMNS = {
    "users": ["token_expiry", "created_at", "updated_at"],
    "git_workspaces": ["created_at", "updated_at", "archived_at", "pull_request_status_last_updated_at"],
}

def convert(raw):
    if raw is None:
        return None
    naive_local = datetime.fromisoformat(raw)
    aware_local = naive_local.replace(tzinfo=LOCAL_TZ, fold=0)
    return aware_local.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")

conn = sqlite3.connect("./ceos_ard_server.db") # Make sure it's the correct name of the SQLite database
cur = conn.cursor()

for table, columns in TABLES_AND_COLUMNS.items():
    rows = cur.execute(f"SELECT id, {', '.join(columns)} FROM {table}").fetchall()
    for row in rows:
        row_id = row[0]
        updates = {col: convert(row[i]) for i, col in enumerate(columns, start=1) if row[i] is not None}
        if updates:
            set_clause = ", ".join(f"{c} = ?" for c in updates)
            cur.execute(f"UPDATE {table} SET {set_clause} WHERE id = ?", (*updates.values(), row_id))

conn.commit()
conn.close()
print("Migration completed.")
