"""
One-off migration: add starring support and saved_generation_profiles table.

- ALTER TABLE saved_filters ADD COLUMN is_starred BOOLEAN NOT NULL DEFAULT FALSE
- Add index ix_saved_filters_is_starred
- Create saved_generation_profiles table (via db.create_all())

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


def run_migration():
    app = create_app()
    with app.app_context():
        print("Running starring migration...")

        if column_exists('saved_filters', 'is_starred'):
            print("  - saved_filters.is_starred already exists, skipping ALTER")
        else:
            print("  - Adding column saved_filters.is_starred ...")
            db.session.execute(text(
                "ALTER TABLE saved_filters ADD COLUMN is_starred BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            db.session.commit()
            print("    done.")

        if index_exists('saved_filters', 'ix_saved_filters_is_starred'):
            print("  - Index ix_saved_filters_is_starred already exists, skipping")
        else:
            print("  - Creating index ix_saved_filters_is_starred ...")
            db.session.execute(text(
                "CREATE INDEX ix_saved_filters_is_starred ON saved_filters (is_starred)"
            ))
            db.session.commit()
            print("    done.")

        print("  - Ensuring saved_generation_profiles table exists ...")
        db.create_all()
        print("    done.")

        print("\nMigration complete.")


if __name__ == '__main__':
    run_migration()
