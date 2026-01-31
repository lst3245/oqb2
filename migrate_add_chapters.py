"""
Migration script to add chapters and subchapters tables
and chapter_id, subchapter_id columns to questions table

Supports both SQLite and MySQL databases.
"""
from sqlalchemy import text, inspect
from app import create_app, db

def migrate():
    """Add chapters and subchapters support to database"""
    app = create_app()
    
    with app.app_context():
        db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        print(f"Migrating database: {db_uri.split('@')[-1] if '@' in db_uri else db_uri}")
        
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()
        
        try:
            # Determine database type
            is_mysql = 'mysql' in db_uri.lower()
            
            # Create chapters table if not exists
            if 'chapters' not in existing_tables:
                print("Creating chapters table...")
                if is_mysql:
                    db.session.execute(text('''
                        CREATE TABLE chapters (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            name VARCHAR(200) NOT NULL
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    '''))
                else:
                    db.session.execute(text('''
                        CREATE TABLE chapters (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name VARCHAR(200) NOT NULL
                        )
                    '''))
                db.session.commit()
                print("[OK] Created chapters table")
            else:
                print("[OK] chapters table already exists")
            
            # Refresh existing tables list
            inspector = inspect(db.engine)
            existing_tables = inspector.get_table_names()
            
            # Create subchapters table if not exists
            if 'subchapters' not in existing_tables:
                print("Creating subchapters table...")
                if is_mysql:
                    db.session.execute(text('''
                        CREATE TABLE subchapters (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            chapter_id INT NOT NULL,
                            name VARCHAR(200) NOT NULL,
                            hidden BOOLEAN NOT NULL DEFAULT FALSE,
                            INDEX ix_subchapters_chapter_id (chapter_id),
                            FOREIGN KEY (chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    '''))
                else:
                    db.session.execute(text('''
                        CREATE TABLE subchapters (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            chapter_id INTEGER NOT NULL,
                            name VARCHAR(200) NOT NULL,
                            hidden BOOLEAN NOT NULL DEFAULT 0,
                            FOREIGN KEY (chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
                        )
                    '''))
                    db.session.execute(text('CREATE INDEX ix_subchapters_chapter_id ON subchapters(chapter_id)'))
                db.session.commit()
                print("[OK] Created subchapters table")
            else:
                print("[OK] subchapters table already exists")
            
            # Check if columns already exist in questions table
            columns = [col['name'] for col in inspector.get_columns('questions')]
            
            # Add chapter_id column to questions table
            if 'chapter_id' not in columns:
                print("Adding chapter_id column to questions...")
                db.session.execute(text('ALTER TABLE questions ADD COLUMN chapter_id INT NULL'))
                if is_mysql:
                    db.session.execute(text('CREATE INDEX ix_questions_chapter_id ON questions(chapter_id)'))
                    db.session.execute(text('ALTER TABLE questions ADD CONSTRAINT fk_questions_chapter FOREIGN KEY (chapter_id) REFERENCES chapters(id) ON DELETE SET NULL'))
                else:
                    db.session.execute(text('CREATE INDEX ix_questions_chapter_id ON questions(chapter_id)'))
                db.session.commit()
                print("[OK] Added chapter_id column")
            else:
                print("[OK] chapter_id column already exists")
            
            # Add subchapter_id column to questions table
            if 'subchapter_id' not in columns:
                print("Adding subchapter_id column to questions...")
                db.session.execute(text('ALTER TABLE questions ADD COLUMN subchapter_id INT NULL'))
                if is_mysql:
                    db.session.execute(text('CREATE INDEX ix_questions_subchapter_id ON questions(subchapter_id)'))
                    db.session.execute(text('ALTER TABLE questions ADD CONSTRAINT fk_questions_subchapter FOREIGN KEY (subchapter_id) REFERENCES subchapters(id) ON DELETE SET NULL'))
                else:
                    db.session.execute(text('CREATE INDEX ix_questions_subchapter_id ON questions(subchapter_id)'))
                db.session.commit()
                print("[OK] Added subchapter_id column")
            else:
                print("[OK] subchapter_id column already exists")
            
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
