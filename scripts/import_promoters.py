"""Import affiliate promoter emails from a CSV file into the database.

Usage:
    python scripts/import_promoters.py [path/to/promoters.csv]

The CSV must have an 'email' column. Only valid email addresses are imported.
Already-present emails are silently skipped (no duplicates).
"""

import sys
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.database import Database


def main() -> None:
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "promoters.csv"

    if not Path(csv_path).exists():
        print(f"Error: file not found: {csv_path}")
        sys.exit(1)

    db = Database()
    db.init_db()

    inserted, skipped_dup, skipped_invalid = db.import_promoters_from_csv(csv_path)

    print(f"Import complete:")
    print(f"  Inserted:         {inserted}")
    print(f"  Already present:  {skipped_dup}")
    print(f"  Invalid/skipped:  {skipped_invalid}")

    total = db.get_promoter_emails()
    print(f"  Total in DB:      {len(total)}")


if __name__ == "__main__":
    main()
