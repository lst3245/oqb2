"""
One-time migration: Create 'saved_filters' and 'generated_files' tables.
Run this once on an existing database. New installs via init_db.py get them automatically.
"""
from app import create_app, db
from sqlalchemy import text


def migrate():
    app = create_app()
    with app.app_context():
        conn = db.engine.connect()

        # Check if saved_filters table already exists
        result = conn.execute(text(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'saved_filters'"
        ))
        if result.scalar() == 0:
            conn.execute(text("""
                CREATE TABLE saved_filters (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    name VARCHAR(200) NOT NULL,
                    filter_data TEXT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX ix_saved_filters_user_id (user_id),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """))
            print("Created 'saved_filters' table.")
        else:
            print("'saved_filters' table already exists.")

        # Check if generated_files table already exists
        result = conn.execute(text(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'generated_files'"
        ))
        if result.scalar() == 0:
            conn.execute(text("""
                CREATE TABLE generated_files (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    display_name VARCHAR(200) NOT NULL,
                    filename VARCHAR(300) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    error_message TEXT NULL,
                    filter_data TEXT NULL,
                    generation_options TEXT NULL,
                    question_count INT DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at DATETIME NULL,
                    INDEX ix_generated_files_user_id (user_id),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """))
            print("Created 'generated_files' table.")
        else:
            print("'generated_files' table already exists.")

        conn.commit()
        conn.close()
        print("Migration complete.")


if __name__ == '__main__':
    migrate()
