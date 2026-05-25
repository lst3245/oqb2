"""
Database initialization script
Creates all tables and inserts default data
"""
from app import create_app, db
from app.models import User, Subject, Topic, Subtopic, SavedFilter, SavedGenerationProfile, SavedQuestionSet, GeneratedFile, SystemSetting

def init_database():
    """Initialize database with schema and default data"""
    app = create_app()
    
    with app.app_context():
        print("Creating database tables...")
        db.create_all()
        print("✓ Tables created")
        
        # Check if subjects already exist
        if Subject.query.count() > 0:
            print("Database already initialized")
            return
        
        # Insert default subjects
        print("Inserting default subjects...")
        subjects = [
            Subject(id='MATC', name='Mathematics Compulsory Part'),
            Subject(id='MAT1', name='Mathematics Module 1 (Calculus and Statistics)'),
            Subject(id='MAT2', name='Mathematics Module 2 (Algebra and Calculus)'),
            Subject(id='ICT', name='Information and Communication Technology'),
        ]
        
        for subject in subjects:
            db.session.add(subject)
        
        db.session.commit()
        print(f"✓ Created {len(subjects)} subjects")
        
        # Create default admin user
        print("Creating default admin user...")
        admin = User(username='admin', is_admin=True)
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print("✓ Admin user created (username: admin, password: admin123)")
        
        # Create sample topics for MATC
        print("Creating sample topics for MATC...")
        sample_topics = [
            ('Number and Algebra', ['Polynomials', 'Equations', 'Inequalities']),
            ('Calculus', ['Differentiation', 'Integration', 'Applications']),
            ('Probability', ['Basic Probability', 'Conditional Probability', 'Distributions']),
            ('Statistics', ['Descriptive Statistics', 'Inferential Statistics']),
        ]
        
        for topic_name, subtopic_names in sample_topics:
            topic = Topic(subject_id='MATC', name=topic_name)
            db.session.add(topic)
            db.session.flush()  # Get topic.id
            
            for subtopic_name in subtopic_names:
                subtopic = Subtopic(topic_id=topic.id, name=subtopic_name)
                db.session.add(subtopic)
        
        db.session.commit()
        print(f"✓ Created {len(sample_topics)} sample topics with subtopics")
        
        print("\n✓✓✓ Database initialization complete! ✓✓✓")
        print("\nYou can now:")
        print("1. Run the ingestor: flask ingest")
        print("2. Start the server: python run.py")
        print("3. Login with: admin / admin123")

if __name__ == '__main__':
    init_database()
