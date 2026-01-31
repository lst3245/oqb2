"""
Migration script to add user permission system:
- Add is_super_admin column to users table
- Create user_subject_permissions table
- Set existing admin user as super_admin
- Grant existing admin users access to all subjects
"""
from sqlalchemy import text, inspect
from app import create_app, db

def migrate():
    """Add user permission system"""
    app = create_app()
    
    with app.app_context():
        db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        print(f"Migrating database: {db_uri.split('@')[-1] if '@' in db_uri else db_uri}")
        
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()
        
        try:
            # 1. Add is_super_admin column to users table
            users_columns = [col['name'] for col in inspector.get_columns('users')]
            
            if 'is_super_admin' not in users_columns:
                print("Adding is_super_admin column to users...")
                db.session.execute(text('ALTER TABLE users ADD COLUMN is_super_admin BOOLEAN NOT NULL DEFAULT FALSE'))
                db.session.commit()
                print("[OK] Added is_super_admin column")
            else:
                print("[OK] is_super_admin column already exists")
            
            # 2. Create user_subject_permissions table
            if 'user_subject_permissions' not in existing_tables:
                print("Creating user_subject_permissions table...")
                db.session.execute(text('''
                    CREATE TABLE user_subject_permissions (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id INT NOT NULL,
                        subject_id VARCHAR(10) NOT NULL,
                        role VARCHAR(10) NOT NULL DEFAULT 'user',
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
                        UNIQUE KEY uq_user_subject (user_id, subject_id),
                        INDEX ix_user_subject_permissions_user_id (user_id),
                        INDEX ix_user_subject_permissions_subject_id (subject_id)
                    )
                '''))
                db.session.commit()
                print("[OK] Created user_subject_permissions table")
            else:
                print("[OK] user_subject_permissions table already exists")
            
            # 3. Set existing admin users as super_admin and grant them access to all subjects
            print("\nUpdating existing admin users...")
            
            # Get all existing admin users
            result = db.session.execute(text('SELECT id, username FROM users WHERE is_admin = TRUE'))
            admin_users = result.fetchall()
            
            if admin_users:
                # Get all subjects
                subjects_result = db.session.execute(text('SELECT id FROM subjects'))
                subjects = [row[0] for row in subjects_result.fetchall()]
                
                for user_id, username in admin_users:
                    # Set as super_admin
                    db.session.execute(text('UPDATE users SET is_super_admin = TRUE WHERE id = :user_id'), {'user_id': user_id})
                    print(f"  [OK] Set {username} as super_admin")
                    
                    # Grant admin access to all subjects
                    for subject_id in subjects:
                        # Check if permission already exists
                        exists = db.session.execute(text(
                            'SELECT id FROM user_subject_permissions WHERE user_id = :user_id AND subject_id = :subject_id'
                        ), {'user_id': user_id, 'subject_id': subject_id}).fetchone()
                        
                        if not exists:
                            db.session.execute(text(
                                'INSERT INTO user_subject_permissions (user_id, subject_id, role) VALUES (:user_id, :subject_id, :role)'
                            ), {'user_id': user_id, 'subject_id': subject_id, 'role': 'admin'})
                    
                    print(f"  [OK] Granted {username} admin access to {len(subjects)} subjects")
                
                db.session.commit()
            else:
                print("  No existing admin users found")
            
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
