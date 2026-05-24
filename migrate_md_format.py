"""
One-off migration: extend QuestionAsset.file_format enum to include 'MD'.

Before: ENUM('IMG', 'DOC')
After:  ENUM('IMG', 'DOC', 'MD')

Safe to run multiple times: checks the current column type and only ALTERs
when 'MD' is missing from the enum definition.
"""
from sqlalchemy import text
from app import create_app, db


TABLE = 'question_assets'
COLUMN = 'file_format'
TARGET_ENUM_SQL = "ENUM('IMG','DOC','MD')"


def current_column_type(table_name, column_name):
    """Return the column TYPE string from information_schema, or None if missing."""
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


def normalise_enum(coltype):
    """Strip whitespace inside an ENUM(...) declaration for robust comparison."""
    if not coltype:
        return ''
    return coltype.replace(' ', '').upper()


def run_migration():
    app = create_app()
    with app.app_context():
        print(f"Running migration: add 'MD' to {TABLE}.{COLUMN} enum ...")

        coltype = current_column_type(TABLE, COLUMN)
        if coltype is None:
            print(f"  ! Column {TABLE}.{COLUMN} does not exist. Did init_db run?")
            return

        print(f"  - Current type: {coltype}")

        if "'MD'" in coltype.upper() or '"MD"' in coltype.upper():
            print("  - 'MD' already present in enum, nothing to do.")
            print("\nMigration complete.")
            return

        normalised = normalise_enum(coltype)
        if not normalised.startswith('ENUM('):
            print(f"  ! Unexpected column type (not an ENUM): {coltype}")
            print("  ! Refusing to ALTER. Inspect manually.")
            return

        print(f"  - Altering {TABLE}.{COLUMN} to {TARGET_ENUM_SQL} NOT NULL ...")
        db.session.execute(text(
            f"ALTER TABLE {TABLE} MODIFY COLUMN {COLUMN} {TARGET_ENUM_SQL} NOT NULL"
        ))
        db.session.commit()
        print("    done.")

        new_type = current_column_type(TABLE, COLUMN)
        print(f"  - New type: {new_type}")
        print("\nMigration complete.")


if __name__ == '__main__':
    run_migration()
