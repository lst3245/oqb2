"""
One-off migration: add starring + sharing support and saved_generation_profiles table.

- ALTER TABLE saved_filters ADD COLUMN is_starred BOOLEAN (+ index)
- ALTER TABLE saved_filters ADD COLUMN is_shared BOOLEAN (+ index)
- Create saved_generation_profiles table (via db.create_all())
- ALTER TABLE saved_generation_profiles ADD COLUMN is_shared BOOLEAN (+ index)
  (only needed for installs created between the starring patch and the sharing patch)

Safe to run multiple times: each step is guarded.
"""
from sqlalchemy import text, inspect
from app import create_app, db
from app.models import SavedFilter, SavedGenerationProfile  # noqa: F401  (registers tables)


def column_exists(table_name, column_name):
    insp = inspect(db.engine)
    if table_name not in insp.get_table_names():
        return False
    return any(c['name'] == column_name for c in insp.get_columns(table_name))


def index_exists(table_name, index_name):
    insp = inspect(db.engine)
    if table_name not in insp.get_table_names():
        return False
    return any(i['name'] == index_name for i in insp.get_indexes(table_name))


def ensure_bool_column(table_name, column_name):
    if column_exists(table_name, column_name):
        print(f"  - {table_name}.{column_name} already exists, skipping ALTER")
        return
    print(f"  - Adding column {table_name}.{column_name} ...")
    db.session.execute(text(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} BOOLEAN NOT NULL DEFAULT FALSE"
    ))
    db.session.commit()
    print("    done.")


def ensure_index(table_name, column_name):
    index_name = f"ix_{table_name}_{column_name}"
    if index_exists(table_name, index_name):
        print(f"  - Index {index_name} already exists, skipping")
        return
    print(f"  - Creating index {index_name} ...")
    db.session.execute(text(
        f"CREATE INDEX {index_name} ON {table_name} ({column_name})"
    ))
    db.session.commit()
    print("    done.")


def run_migration():
    app = create_app()
    with app.app_context():
        print("Running starring + sharing migration...")

        # saved_filters: add is_starred and is_shared
        ensure_bool_column('saved_filters', 'is_starred')
        ensure_index('saved_filters', 'is_starred')
        ensure_bool_column('saved_filters', 'is_shared')
        ensure_index('saved_filters', 'is_shared')

        # Ensure saved_generation_profiles table exists (db.create_all handles this)
        print("  - Ensuring saved_generation_profiles table exists ...")
        db.create_all()
        print("    done.")

        # For installs that ran an earlier version of this script (before is_shared
        # was part of the model), backfill is_shared on saved_generation_profiles.
        ensure_bool_column('saved_generation_profiles', 'is_shared')
        ensure_index('saved_generation_profiles', 'is_shared')

        print("\nMigration complete.")


if __name__ == '__main__':
    run_migration()
