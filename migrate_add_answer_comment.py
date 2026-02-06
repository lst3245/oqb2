"""
One-time migration: Add 'answer' and 'comment' columns to the questions table.
Run this once on an existing database. New installs via init_db.py get them automatically.
"""
from app import create_app, db
from sqlalchemy import text

def migrate():
    app = create_app()
    with app.app_context():
        conn = db.engine.connect()
        # Check if columns already exist (MariaDB/MySQL)
        result = conn.execute(text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME='questions' AND COLUMN_NAME IN ('answer','comment')"
        ))
        existing = {row[0] for row in result}

        if 'answer' not in existing:
            conn.execute(text("ALTER TABLE questions ADD COLUMN answer TEXT NULL"))
            print("Added 'answer' column.")
        else:
            print("'answer' column already exists.")

        if 'comment' not in existing:
            conn.execute(text("ALTER TABLE questions ADD COLUMN comment TEXT NULL"))
            print("Added 'comment' column.")
        else:
            print("'comment' column already exists.")

        conn.commit()
        conn.close()
        print("Migration complete.")

if __name__ == '__main__':
    migrate()
