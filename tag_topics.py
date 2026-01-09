"""
Tag DSE P2 questions with major topics and sections from CSV file
"""
import csv
from pathlib import Path
from app import create_app
from app.models import db, Question, Topic

# Configuration
CSV_PATH = Path(r"Q:\Temp\question_topics.csv")
SUBJECT = "MATC"
SOURCE = "DSE"
PAPER = "P2"

# Mapping for junior topics (J1-J8) to database IDs
JUNIOR_TOPIC_MAP = {
    'J1': 24,  # Basic Algebra
    'J2': 25,  # Percentages
    'J3': 26,  # Estimation
    'J4': 27,  # Rate, Ratio and Proportion
    'J5': 28,  # Mensuration
    'J6': 29,  # Numeral System
    'J7': 30,  # Polar Coordinates
    'J8': 31   # Polygon and Symmetry
}

def read_topic_data():
    """Read topic assignments from CSV file."""
    print("Reading topic data from CSV...")
    
    topics_data = {}  # {(year, qno): (topic_id, section)}
    
    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        # Skip header rows
        next(reader)  # Version line
        next(reader)  # Column headers
        
        for row in reader:
            if len(row) < 4:
                continue
            
            year = row[0].strip()
            qno = row[1].strip()
            topic_num = row[2].strip()
            section = row[3].strip()
            
            if not year or not qno or not topic_num:
                continue
            
            # Convert topic_num to database ID
            if topic_num.startswith('J'):
                # Junior topic
                topic_id = JUNIOR_TOPIC_MAP.get(topic_num)
                if not topic_id:
                    print(f"  [WARN] Unknown junior topic: {topic_num} for {year} Q{qno}")
                    continue
            else:
                # Regular topic (1-23)
                try:
                    topic_id = int(topic_num)
                    if topic_id < 1 or topic_id > 23:
                        print(f"  [WARN] Topic ID out of range: {topic_id} for {year} Q{qno}")
                        continue
                except ValueError:
                    print(f"  [WARN] Invalid topic number: {topic_num} for {year} Q{qno}")
                    continue
            
            topics_data[(year, qno)] = (topic_id, section)
    
    print(f"  Loaded {len(topics_data)} topic assignments")
    return topics_data

def update_questions(topics_data):
    """Update questions in database with topic and section info."""
    print("\nUpdating questions in database...")
    
    app = create_app()
    with app.app_context():
        stats = {
            'updated': 0,
            'not_found': 0,
            'errors': []
        }
        
        for (year, qno), (topic_id, section) in topics_data.items():
            # Construct QID
            qid = f"{SUBJECT}_{SOURCE}_{year}_{PAPER}_Q{qno}"
            
            # Find question
            question = Question.query.filter_by(qid=qid).first()
            
            if not question:
                stats['not_found'] += 1
                if stats['not_found'] <= 5:  # Show first 5 examples
                    print(f"  [WARN] Question not found: {qid}")
                continue
            
            # Verify topic exists
            topic = Topic.query.get(topic_id)
            if not topic:
                stats['errors'].append(f"Topic ID {topic_id} not found in database for {qid}")
                continue
            
            # Update question
            question.major_topic_id = topic_id
            question.section = section
            stats['updated'] += 1
            
            # Show progress for first few
            if stats['updated'] <= 10:
                print(f"  [OK] {qid}: Topic={topic.name}, Section={section}")
        
        # Show summary if more than 10
        if stats['updated'] > 10:
            print(f"  ... and {stats['updated'] - 10} more questions")
        
        # Commit changes
        try:
            db.session.commit()
            print(f"\n[SUCCESS] Database updated!")
            print(f"  Updated: {stats['updated']}")
            print(f"  Not found: {stats['not_found']}")
            if stats['errors']:
                print(f"  Errors: {len(stats['errors'])}")
                for error in stats['errors'][:5]:
                    print(f"    - {error}")
        except Exception as e:
            db.session.rollback()
            print(f"\n[ERROR] Failed to commit changes: {e}")
            return
        
        # Verify results
        print("\nVerifying updates...")
        sample_qids = [
            "MATC_DSE_2024_P2_Q1",
            "MATC_DSE_2024_P2_Q15",
            "MATC_DSE_2024_P2_Q31"
        ]
        
        for qid in sample_qids:
            q = Question.query.filter_by(qid=qid).first()
            if q:
                topic_name = q.major_topic.name if q.major_topic else "None"
                print(f"  {qid}: Topic={topic_name}, Section={q.section}")

def main():
    """Main execution function."""
    print("="*80)
    print("DSE P2 Topic Tagging Script")
    print("="*80)
    
    # Step 1: Read CSV data
    topics_data = read_topic_data()
    
    if not topics_data:
        print("\n[ERROR] No topic data loaded from CSV")
        return
    
    # Step 2: Update questions
    update_questions(topics_data)
    
    print("\n" + "="*80)
    print("Tagging completed!")
    print("="*80)
    print("\nYou can now:")
    print("  1. View questions in the web application with their topics")
    print("  2. Filter questions by topic in the dashboard")
    print("  3. Use the topic information for generating targeted practice papers")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nProcess interrupted by user.")
    except Exception as e:
        print(f"\n\n[FATAL ERROR]: {e}")
        import traceback
        traceback.print_exc()
