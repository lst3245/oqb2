"""
Migration script to add subject_id column to chapters table
"""
from sqlalchemy import text, inspect
from app import create_app, db

def migrate():
    """Add subject_id to chapters table"""
    app = create_app()
    
    with app.app_context():
        db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        print(f"Migrating database: {db_uri.split('@')[-1] if '@' in db_uri else db_uri}")
        
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('chapters')]
        
        try:
            if 'subject_id' not in columns:
                print("Adding subject_id column to chapters...")
                # Add column with default value for existing rows
                db.session.execute(text("ALTER TABLE chapters ADD COLUMN subject_id VARCHAR(10) NOT NULL DEFAULT 'MATC'"))
                db.session.execute(text('CREATE INDEX ix_chapters_subject_id ON chapters(subject_id)'))
                db.session.commit()
                print("[OK] Added subject_id column to chapters")
            else:
                print("[OK] subject_id column already exists in chapters")
            
            print("\n=== Migration completed successfully! ===")
            return True
            
        except Exception as e:
            db.session.rollback()
            print(f"Error during migration: {e}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == '__main__':
    migrate()
