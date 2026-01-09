"""
DSE P2 Question Import Script
This script processes DSE P2 questions from Q:\Temp and imports them into the question bank system.

It performs the following operations:
1. Reads answer and percentage data from CSV files
2. Creates proper folder structure in Source/MATC/PP/DSE/
3. Copies and renames QUE files according to naming convention
4. Copies and renames appropriate answer PNG files (A/B/C/D)
5. Ingests all files into the database
6. Updates questions with correct percentages
"""

import os
import shutil
import csv
from pathlib import Path
import re
from app import create_app
from app.models import db, Question

# Configuration
SOURCE_TEMP = Path(r"Q:\Temp")
QUE_FOLDER = SOURCE_TEMP / "QUE"
ANS_CSV = SOURCE_TEMP / "MATC MC ANS.csv"
PERCENTAGE_CSV = SOURCE_TEMP / "MATC MC Percentage.csv"
LETTER_IMAGES = {
    'A': SOURCE_TEMP / "A.png",
    'B': SOURCE_TEMP / "B.png",
    'C': SOURCE_TEMP / "C.png",
    'D': SOURCE_TEMP / "D.png"
}

# Target directory structure (using Q:\Source as configured in .env)
SOURCE_ROOT = Path(r"Q:\Source") / "MATC" / "PP" / "DSE"

# Subject and paper info
SUBJECT = "MATC"
SOURCE = "DSE"
PAPER = "P2"
LANGUAGE = "EN"  # Default language


def read_csv_data():
    """Read answer and percentage data from CSV files."""
    print("Reading CSV files...")
    
    # Read answers
    answers = {}  # {year: {qno: answer_letter}}
    with open(ANS_CSV, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader)  # First row has years
        years = [h for h in headers[1:] if h]  # Skip first empty column
        
        for row in reader:
            qno = row[0]
            if not qno:
                continue
            for i, answer in enumerate(row[1:], 0):
                if i < len(years) and answer:
                    year = years[i]
                    if year not in answers:
                        answers[year] = {}
                    answers[year][qno] = answer.strip()
    
    # Read percentages
    percentages = {}  # {year: {qno: percentage}}
    with open(PERCENTAGE_CSV, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader)
        years = [h for h in headers[1:] if h]
        
        for row in reader:
            qno = row[0]
            if not qno:
                continue
            for i, pct in enumerate(row[1:], 0):
                if i < len(years) and pct and pct != 'no data':
                    year = years[i]
                    if year not in percentages:
                        percentages[year] = {}
                    # Remove % sign and convert to float
                    pct_value = pct.strip().replace('%', '')
                    try:
                        percentages[year][qno] = float(pct_value)
                    except ValueError:
                        pass
    
    print(f"  Loaded answers for {len(answers)} years")
    print(f"  Loaded percentages for {len(percentages)} years")
    return answers, percentages


def create_folder_structure():
    """Create the Source directory structure."""
    print("\nCreating folder structure...")
    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"  Created: {SOURCE_ROOT}")


def get_que_files():
    """Get all QUE files from Q:\Temp\QUE."""
    print("\nScanning QUE files...")
    que_files = list(QUE_FOLDER.glob("*.png"))
    print(f"  Found {len(que_files)} PNG files")
    return que_files


def parse_filename(filename):
    """Parse filename to extract year and question number.
    Expected format: YEAR_QNO.png (e.g., 2012_04.png)
    """
    match = re.match(r'(\d{4})_(\d+)\.png', filename)
    if match:
        year = match.group(1)
        qno = int(match.group(2))  # Convert to int to remove leading zeros
        return year, qno
    return None, None


def copy_and_rename_files(que_files, answers):
    """Copy and rename QUE files and corresponding ANS files."""
    print("\nCopying and renaming files...")
    
    stats = {
        'que_copied': 0,
        'ans_copied': 0,
        'no_answer': 0,
        'errors': []
    }
    
    for que_file in que_files:
        year, qno = parse_filename(que_file.name)
        if not year or not qno:
            stats['errors'].append(f"Could not parse filename: {que_file.name}")
            continue
        
        # Create year/paper directory
        target_dir = SOURCE_ROOT / year / PAPER
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # Format question number with Q prefix (Q1, Q2, Q10, etc.)
        qno_str = f"Q{qno}"
        
        # Copy QUE file with new naming convention
        # Format: MATC_DSE_2025_P2_Q5_EN_QUE.png
        que_target_name = f"{SUBJECT}_{SOURCE}_{year}_{PAPER}_{qno_str}_{LANGUAGE}_QUE.png"
        que_target_path = target_dir / que_target_name
        
        try:
            shutil.copy2(que_file, que_target_path)
            stats['que_copied'] += 1
            print(f"  [OK] {que_file.name} -> {que_target_name}")
        except Exception as e:
            stats['errors'].append(f"Error copying QUE {que_file.name}: {e}")
            continue
        
        # Copy ANS file if answer exists
        if year in answers and str(qno) in answers[year]:
            answer_letter = answers[year][str(qno)]
            if answer_letter in LETTER_IMAGES:
                letter_img = LETTER_IMAGES[answer_letter]
                if letter_img.exists():
                    ans_target_name = f"{SUBJECT}_{SOURCE}_{year}_{PAPER}_{qno_str}_{LANGUAGE}_ANS.png"
                    ans_target_path = target_dir / ans_target_name
                    
                    try:
                        shutil.copy2(letter_img, ans_target_path)
                        stats['ans_copied'] += 1
                        print(f"    [OK] Answer {answer_letter} -> {ans_target_name}")
                    except Exception as e:
                        stats['errors'].append(f"Error copying ANS for {year} Q{qno}: {e}")
                else:
                    stats['errors'].append(f"Letter image not found: {letter_img}")
        else:
            stats['no_answer'] += 1
            print(f"    [WARN] No answer found for {year} Q{qno}")
    
    print(f"\n  Summary:")
    print(f"    QUE files copied: {stats['que_copied']}")
    print(f"    ANS files copied: {stats['ans_copied']}")
    print(f"    Questions without answers: {stats['no_answer']}")
    if stats['errors']:
        print(f"    Errors: {len(stats['errors'])}")
        for error in stats['errors'][:10]:  # Show first 10 errors
            print(f"      - {error}")
    
    return stats


def run_ingestor():
    """Run the ingestor to import files into database."""
    print("\n" + "="*80)
    print("Running ingestor to import files into database...")
    print("="*80)
    
    from app.ingestor import ingest_command
    
    # Run ingestor command with Q:\Source as base (so paths include MATC\PP\DSE\)
    ingest_command(r"Q:\Source")
    
    print("\nIngestor completed!")


def update_percentages(percentages):
    """Update questions with correct percentages."""
    print("\n" + "="*80)
    print("Updating correct percentages in database...")
    print("="*80)
    
    app = create_app()
    with app.app_context():
        stats = {
            'updated': 0,
            'not_found': 0,
            'skipped': 0
        }
        
        for year, qnos in percentages.items():
            for qno, pct in qnos.items():
                # Construct QID: MATC_DSE_2025_P2_Q5
                qno_str = f"Q{qno}"
                qid = f"{SUBJECT}_{SOURCE}_{year}_{PAPER}_{qno_str}"
                
                # Find question in database
                question = Question.query.filter_by(qid=qid).first()
                if question:
                    # Only update if percentage is different
                    if question.correct_percentage != pct:
                        question.correct_percentage = pct
                        stats['updated'] += 1
                        print(f"  [OK] Updated {qid}: {pct}%")
                    else:
                        stats['skipped'] += 1
                else:
                    stats['not_found'] += 1
                    print(f"  [WARN] Question not found: {qid}")
        
        # Commit all changes
        try:
            db.session.commit()
            print(f"\n  Summary:")
            print(f"    Updated: {stats['updated']}")
            print(f"    Skipped (already set): {stats['skipped']}")
            print(f"    Not found: {stats['not_found']}")
        except Exception as e:
            db.session.rollback()
            print(f"\n  [ERROR] Error committing changes: {e}")


def main():
    """Main execution function."""
    print("="*80)
    print("DSE P2 Question Import Script")
    print("="*80)
    
    # Step 1: Read CSV data
    answers, percentages = read_csv_data()
    
    # Step 2: Create folder structure
    create_folder_structure()
    
    # Step 3: Get QUE files
    que_files = get_que_files()
    
    # Step 4: Copy and rename files
    stats = copy_and_rename_files(que_files, answers)
    
    # Step 5: Run ingestor
    if stats['que_copied'] > 0:
        run_ingestor()
        
        # Step 6: Update percentages
        update_percentages(percentages)
    else:
        print("\n[WARN] No files were copied. Skipping ingestor and percentage update.")
    
    print("\n" + "="*80)
    print("Import process completed!")
    print("="*80)
    print("\nYou can now:")
    print("  1. Check the Q:\\Source\\MATC\\PP\\DSE\\ directory for imported files")
    print("  2. Log into the web application to view the questions")
    print("  3. Use Admin > Tag Questions to add topics and metadata")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nProcess interrupted by user.")
    except Exception as e:
        print(f"\n\n[FATAL ERROR]: {e}")
        import traceback
        traceback.print_exc()
