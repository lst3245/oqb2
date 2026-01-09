# DSE P2 Question Import Guide

## Overview

This guide explains how to use the `import_dse_p2.py` script to import DSE Paper 2 questions from Q:\Temp into your question bank system.

## Prerequisites

Before running the script, ensure you have:

1. **Source files in Q:\Temp:**
   - `QUE\` folder with PNG files (format: `YYYY_NN.png`, e.g., `2012_04.png`)
   - `MATC MC ANS.csv` with answer data
   - `MATC MC Percentage.csv` with correct percentage data
   - `A.png`, `B.png`, `C.png`, `D.png` (letter images for answers)

2. **Database configured:**
   - `.env` file with database credentials
   - Database initialized (`python init_db.py`)

3. **Q:\Source directory:**
   - Directory exists (configured as SOURCE_PATH in .env)
   - Write permissions

## What the Script Does

The script performs the following operations in sequence:

### 1. Read CSV Data
- Loads answer keys from `MATC MC ANS.csv`
- Loads correct percentages from `MATC MC Percentage.csv`
- Supports years 2012-2025

### 2. Create Folder Structure
- Creates: `Q:\Source\MATC\PP\DSE\YYYY\P2\` for each year
- Example: `Q:\Source\MATC\PP\DSE\2024\P2\`

### 3. Copy and Rename QUE Files
- Original: `2024_05.png`
- Renamed to: `MATC_DSE_2024_P2_Q5_EN_QUE.png`

### 4. Copy and Rename ANS Files
- Looks up answer from CSV (e.g., answer is "C")
- Copies `C.png` to: `MATC_DSE_2024_P2_Q5_EN_ANS.png`

### 5. Run Ingestor
- Automatically scans the new files
- Creates Question records in database
- Links QuestionAsset records

### 6. Update Percentages
- Looks up each question by QID
- Updates `correct_percentage` field in database

## Running the Script

### Method 1: Direct Python Execution

```powershell
# Make sure virtual environment is activated
.\venv\Scripts\Activate.ps1

# Run the script
python import_dse_p2.py
```

### Method 2: From IDE
- Open `import_dse_p2.py` in your editor
- Run it directly (F5 or Run button)

## Expected Output

The script will show progress for each step:

```
================================================================================
DSE P2 Question Import Script
================================================================================
Reading CSV files...
  Loaded answers for 14 years
  Loaded percentages for 14 years

Creating folder structure...
  Created: Q:\Source\MATC\PP\DSE

Scanning QUE files...
  Found 630 PNG files

Copying and renaming files...
  ✓ 2012_04.png -> MATC_DSE_2012_P2_Q4_EN_QUE.png
    ✓ Answer B -> MATC_DSE_2012_P2_Q4_EN_ANS.png
  ✓ 2012_08.png -> MATC_DSE_2012_P2_Q8_EN_QUE.png
    ✓ Answer D -> MATC_DSE_2012_P2_Q8_EN_ANS.png
  ...

  Summary:
    QUE files copied: 630
    ANS files copied: 550
    Questions without answers: 80

================================================================================
Running ingestor to import files into database...
================================================================================
...

================================================================================
Updating correct percentages in database...
================================================================================
  ✓ Updated MATC_DSE_2012_P2_Q1: 94.0%
  ✓ Updated MATC_DSE_2012_P2_Q2: 74.0%
  ...

================================================================================
Import process completed!
================================================================================
```

## File Naming Convention

The script follows your project's naming convention:

```
MATC_DSE_2024_P2_Q5_EN_QUE.png
└─┬┘ └┬┘ └─┬┘ └┬ └┬ └┬ └─┬ └─┬┘
  │   │    │   │  │  │   │   └─ Extension (png)
  │   │    │   │  │  │   └───── Type (QUE/ANS)
  │   │    │   │  │  └─────────Language (EN)
  │   │    │   │  └────────────Question number (Q1, Q2, Q10)
  │   │    │   └───────────────Paper (P2)
  │   │    └───────────────────Year (2024)
  │   └────────────────────────Source (DSE)
  └────────────────────────────Subject (MATC)
```

## Verification

After the script completes, you can verify:

### 1. Check Files
```powershell
# View created files for a specific year
Get-ChildItem "Q:\Source\MATC\PP\DSE\2024\P2\" | Select-Object Name
```

### 2. Check Database
- Start the web application: `python run.py`
- Navigate to http://localhost:5000
- Login with admin credentials
- Go to Dashboard
- Filter by:
  - Subject: MATC
  - Source: DSE
  - Year: 2024
  - Paper/Section: P2

### 3. Verify Data
- Questions should show up in the dashboard
- Preview QUE images should load
- Preview ANS images should load
- Correct percentages should be displayed (if available)

## Troubleshooting

### Issue: CSV files not found
**Solution:** Ensure CSV files are in Q:\Temp with exact names:
- `MATC MC ANS.csv`
- `MATC MC Percentage.csv`

### Issue: Letter images not found
**Solution:** Ensure A.png, B.png, C.png, D.png exist in Q:\Temp

### Issue: Database connection error
**Solution:** Check .env file has correct DB credentials

### Issue: Permission denied
**Solution:** Ensure you have write access to Q:\Source directory

### Issue: No files copied
**Solution:** Check QUE folder path and file naming format (YYYY_NN.png)

### Issue: Questions not showing in database
**Solution:** Run ingestor manually:
```powershell
python cli.py ingest
```

## Configuration Options

You can modify the script to customize:

### Change Language
```python
LANGUAGE = "CH"  # For Chinese, or "BI" for Bilingual
```

### Change Paper
```python
PAPER = "P1"  # For Paper 1
```

### Change Subject
```python
SUBJECT = "MAT1"  # For M1, or "MAT2" for M2
```

## After Import

Once import is complete:

1. **View Questions:** Dashboard → Filter by DSE P2
2. **Add Topics:** Admin → Manage Topics → Create topics
3. **Tag Questions:** Admin → Tag Questions → Assign topics and metadata
4. **Generate Papers:** Dashboard → Select questions → Generate document

## Notes

- The script is idempotent (safe to run multiple times)
- Existing files will be overwritten if script is re-run
- Database upsert logic prevents duplicate questions
- Questions without answers will still be imported (QUE only)
- "no data" percentages in CSV will be skipped

## Support

If you encounter issues:
1. Check the console output for error messages
2. Review `ingest_errors.log` for ingestion issues
3. Verify file naming conventions match exactly
4. Ensure database is properly initialized

## Script Modifications

The script can be easily adapted for:
- Different subjects (MATC, MAT1, MAT2, ICT)
- Different sources (DSE, CE, AL)
- Different papers (P1, P2)
- Different file formats (JPG, DOCX)

To modify, edit the configuration section at the top of `import_dse_p2.py`.
