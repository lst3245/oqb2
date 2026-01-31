"""
Migration script to add sort_order column to topics, subtopics, chapters, subchapters tables
"""
from sqlalchemy import text, inspect
from app import create_app, db

def migrate():
    """Add sort_order columns for custom ordering"""
    app = create_app()
    
    with app.app_context():
        db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        print(f"Migrating database: {db_uri.split('@')[-1] if '@' in db_uri else db_uri}")
        
        inspector = inspect(db.engine)
        
        tables_to_update = ['topics', 'subtopics', 'chapters', 'subchapters']
        
        try:
            for table in tables_to_update:
                columns = [col['name'] for col in inspector.get_columns(table)]
                
                if 'sort_order' not in columns:
                    print(f"Adding sort_order column to {table}...")
                    # Add column with default 0
                    db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN sort_order INT NOT NULL DEFAULT 0'))
                    # Set existing rows' sort_order to their ID for initial ordering
                    db.session.execute(text(f'UPDATE {table} SET sort_order = id'))
                    db.session.commit()
                    print(f"[OK] Added sort_order to {table}")
                else:
                    print(f"[OK] sort_order column already exists in {table}")
            
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
