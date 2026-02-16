"""
One-time migration script to add a unique constraint on question_assets table
for (question_id, asset_type, language, file_format, part_number).

This prevents duplicate asset records from being created by concurrent operations.

Usage:
    python migrate_asset_unique_constraint.py
"""
from app import create_app, db
from sqlalchemy import text


def migrate():
    app = create_app()

    with app.app_context():
        conn = db.engine.connect()

        # Check if constraint already exists
        result = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.table_constraints "
            "WHERE table_schema = DATABASE() "
            "AND table_name = 'question_assets' "
            "AND constraint_name = 'uq_asset_identity'"
        ))
        exists = result.scalar() > 0

        if exists:
            print("Unique constraint 'uq_asset_identity' already exists. Nothing to do.")
            conn.close()
            return

        # First check for any existing duplicates that would violate the constraint
        print("Checking for existing duplicates...")
        dups = conn.execute(text(
            "SELECT question_id, asset_type, language, file_format, part_number, COUNT(*) as cnt "
            "FROM question_assets "
            "GROUP BY question_id, asset_type, language, file_format, part_number "
            "HAVING COUNT(*) > 1"
        ))
        dup_rows = dups.fetchall()

        if dup_rows:
            print(f"WARNING: Found {len(dup_rows)} duplicate groups. Cleaning up (keeping latest record)...")
            for row in dup_rows:
                # Keep the record with the highest ID (most recent), delete the rest
                delete_result = conn.execute(text(
                    "DELETE a FROM question_assets a "
                    "INNER JOIN question_assets b "
                    "ON a.question_id = b.question_id "
                    "AND a.asset_type = b.asset_type "
                    "AND a.language = b.language "
                    "AND a.file_format = b.file_format "
                    "AND a.part_number = b.part_number "
                    "AND a.id < b.id "
                    "WHERE a.question_id = :qid "
                    "AND a.asset_type = :atype "
                    "AND a.language = :lang "
                    "AND a.file_format = :fmt "
                    "AND a.part_number = :pnum"
                ), {
                    'qid': row[0], 'atype': row[1], 'lang': row[2],
                    'fmt': row[3], 'pnum': row[4]
                })
                print(f"  Cleaned: question_id={row[0]} {row[1]}_{row[2]}_P{row[4]} "
                      f"(removed {row[5] - 1} duplicate(s))")
            conn.commit()

        print("Adding unique constraint 'uq_asset_identity'...")
        conn.execute(text(
            "ALTER TABLE question_assets "
            "ADD CONSTRAINT uq_asset_identity "
            "UNIQUE (question_id, asset_type, language, file_format, part_number)"
        ))
        conn.commit()
        conn.close()
        print("Migration complete!")


if __name__ == '__main__':
    migrate()
