"""
One-off migration: rename QuestionAsset.language -> version and extend the
enum to include the official public-exam versions ENO / CHO.

Before: column `language` ENUM('EN','CH','BI')
After:  column `version`  ENUM('EN','CH','BI','ENO','CHO')

Safe to run multiple times:
- If `language` still exists, it is renamed to `version` via CHANGE COLUMN
  (MariaDB/MySQL carries the unique index `uq_asset_identity` over to the new
  column name automatically).
- If `version` already exists but with a smaller enum, it is widened.
- If `version` already has all five values, nothing is done.
"""
from sqlalchemy import text
from app import create_app, db


TABLE = 'question_assets'
OLD_COLUMN = 'language'
NEW_COLUMN = 'version'
TARGET_ENUM_SQL = "ENUM('EN','CH','BI','ENO','CHO')"
REQUIRED_VALUES = ("'EN'", "'CH'", "'BI'", "'ENO'", "'CHO'")


def current_column_type(table_name, column_name):
    """Return the COLUMN_TYPE string from information_schema, or None if missing."""
    row = db.session.execute(text(
        """
        SELECT COLUMN_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = :t
          AND COLUMN_NAME  = :c
        """
    ), {'t': table_name, 'c': column_name}).first()
    return row[0] if row else None


def _enum_has_all(coltype):
    up = (coltype or '').upper()
    return all(v in up for v in REQUIRED_VALUES)


def run_migration():
    app = create_app()
    with app.app_context():
        print(f"Running migration: {TABLE}.{OLD_COLUMN} -> {NEW_COLUMN} {TARGET_ENUM_SQL} ...")

        old_type = current_column_type(TABLE, OLD_COLUMN)
        new_type = current_column_type(TABLE, NEW_COLUMN)

        if old_type is None and new_type is None:
            print(f"  ! Neither `{OLD_COLUMN}` nor `{NEW_COLUMN}` exists on {TABLE}. Did init_db run?")
            return

        if old_type is not None:
            print(f"  - Found legacy column `{OLD_COLUMN}` ({old_type}); renaming + widening ...")
            db.session.execute(text(
                f"ALTER TABLE {TABLE} CHANGE COLUMN {OLD_COLUMN} {NEW_COLUMN} {TARGET_ENUM_SQL} NOT NULL"
            ))
            db.session.commit()
            print("    done.")
        elif not _enum_has_all(new_type):
            print(f"  - Column `{NEW_COLUMN}` exists ({new_type}) but missing some values; widening ...")
            db.session.execute(text(
                f"ALTER TABLE {TABLE} MODIFY COLUMN {NEW_COLUMN} {TARGET_ENUM_SQL} NOT NULL"
            ))
            db.session.commit()
            print("    done.")
        else:
            print(f"  - Column `{NEW_COLUMN}` already up to date ({new_type}); nothing to do.")

        print(f"  - Final type: {current_column_type(TABLE, NEW_COLUMN)}")
        print("\nMigration complete.")


if __name__ == '__main__':
    run_migration()
