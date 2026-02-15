"""
One-time migration script to add part_number column to question_assets table.
Run this once after updating the code to support multi-image questions.

Usage:
    python migrate_part_number.py
"""
from app import create_app, db
from sqlalchemy import text

def migrate():
    app = create_app()
    
    with app.app_context():
        conn = db.engine.connect()
        
        # Check if column already exists
        result = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() "
            "AND table_name = 'question_assets' "
            "AND column_name = 'part_number'"
        ))
        exists = result.scalar() > 0
        
        if exists:
            print("Column 'part_number' already exists. Nothing to do.")
            conn.close()
            return
        
        print("Adding 'part_number' column to question_assets table...")
        conn.execute(text(
            "ALTER TABLE question_assets ADD COLUMN part_number INTEGER NOT NULL DEFAULT 1"
        ))
        conn.commit()
        
        # Verify
        result = conn.execute(text("SELECT COUNT(*) FROM question_assets"))
        count = result.scalar()
        print(f"Updated {count} existing rows with part_number = 1")
        
        conn.close()
        print("Migration complete!")

if __name__ == '__main__':
    migrate()
