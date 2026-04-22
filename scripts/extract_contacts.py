"""
Standalone script: extract contact emails from channel descriptions already in the DB.
Updates channels.contact_email for all channels where a description contains an email.

Usage:
    python extract_contacts.py
"""
import sqlite3
from src.db.database import Database
from src.nodes.generate_emails import _extract_email

db = Database()
db.init_db()
db.migrate_add_contact_email()

conn = sqlite3.connect(db.db_path)
rows = conn.execute("SELECT channel_id, description FROM channels WHERE description IS NOT NULL").fetchall()

updated = 0
for channel_id, description in rows:
    email = _extract_email(description)
    if email:
        conn.execute(
            "UPDATE channels SET contact_email = ? WHERE channel_id = ?",
            (email, channel_id),
        )
        updated += 1

conn.commit()
conn.close()

print(f"Done — {updated}/{len(rows)} channels had a contact email in their description.")
print("\nQuery results:")
import subprocess
subprocess.run([
    "sqlite3", db.db_path,
    "SELECT channel_title, contact_email FROM channels WHERE contact_email IS NOT NULL LIMIT 20;"
])
