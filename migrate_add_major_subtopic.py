"""
Migration script to add major_subtopic_id column to questions table
Run this once after updating the models.
"""
from app import create_app, db
from sqlalchemy import text

def run_migration():
    """Add major_subtopic_id column to questions table"""
    app = create_app()
    
    with app.app_context():
        print("Running migration: Add major_subtopic_id to questions table...")
        
        # Check if column already exists
        try:
            result = db.session.execute(text(
                "SELECT major_subtopic_id FROM questions LIMIT 1"
            ))
            print("[OK] Column major_subtopic_id already exists")
            return
        except Exception as e:
            # Column doesn't exist, proceed with migration
            pass
        
        # Add the column
        try:
            db.session.execute(text(
                "ALTER TABLE questions ADD COLUMN major_subtopic_id INTEGER NULL"
            ))
            db.session.commit()
            print("[OK] Added major_subtopic_id column")
            
            # Add foreign key constraint (MySQL syntax)
            try:
                db.session.execute(text(
                    "ALTER TABLE questions ADD CONSTRAINT fk_questions_major_subtopic "
                    "FOREIGN KEY (major_subtopic_id) REFERENCES subtopics(id)"
                ))
                db.session.commit()
                print("[OK] Added foreign key constraint")
            except Exception as e:
                print(f"Note: Could not add foreign key constraint (may already exist): {e}")
            
            # Add index for better query performance
            try:
                db.session.execute(text(
                    "CREATE INDEX ix_questions_major_subtopic_id ON questions(major_subtopic_id)"
                ))
                db.session.commit()
                print("[OK] Added index on major_subtopic_id")
            except Exception as e:
                print(f"Note: Could not add index (may already exist): {e}")
            
            print("\n=== Migration complete! ===")
            
        except Exception as e:
            db.session.rollback()
            print(f"Error during migration: {e}")
            raise

if __name__ == '__main__':
    run_migration()
