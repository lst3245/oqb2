# Changelog

All notable changes to the Online Question Bank System are documented in this file.

## [2.0.0] - 2026-01-09

### 🎉 Major Features Added

#### Multi-level Sorting
- **Dashboard Sorting**: Sort questions by multiple fields with custom priority
  - Example: Topic → Level → Year
  - Available fields: QID, Year, Level, Topic, Subtopic, Source, Section, Type, Created Time
  - Natural sorting for text fields (Q1, Q2, Q10 instead of Q1, Q10, Q2)
  - Sort configuration persists in session

#### Batch Operations
- **Batch Update**: Update metadata for multiple questions simultaneously
  - Selectively update: level, question type, section, or topic/subtopic relationships
  - Checkbox system to choose which fields to update
- **Batch Delete**: Permanently remove multiple questions at once
  - Confirmation dialog to prevent accidental deletion
  - Cascade deletion of associated assets
  - Returns count and list of deleted QIDs

#### Database Sync
- **Sync Command**: Remove orphaned database records when source files are deleted
  - Dry-run mode to preview what would be deleted
  - Detects orphaned assets (files that no longer exist)
  - Detects orphaned questions (questions with no assets)
  - CLI: `python cli.py sync` (preview) or `python cli.py sync --no-dry-run` (delete)

#### Smart Document Spacing
- **Separate MC and CQ Settings**: Different spacing for multiple choice vs conventional questions
  - Before question: Skip N lines OR start from new page
  - After question: Skip N lines OR start from new page
  - Intelligent page break handling (avoids duplicate breaks)
- **Flexible Sort Modes**: 
  - Preserve selection order
  - Custom multi-level sort

### ✨ Enhanced Features

#### Dashboard Enhancements
- **Direct QID Search**: Search by question ID with wildcard support
  - Example: `MATC_DSE_2024*` finds all 2024 DSE MATC questions
  - Supports SQL wildcards (`%` or `*`)
- **Topic Mode**: AND/OR logic for multi-topic filtering
  - AND mode: Questions must have ALL selected topics
  - OR mode: Questions can have ANY selected topics (default)
- **Preview Language Preference**: Prioritize English or Chinese in preview
  - Order: Preferred language → Bilingual → Other language
- **Configurable Page Size**: Choose 10, 20, 50, or 100 questions per page
- **Dynamic Year Loading**: Years are loaded based on selected subject and source
- **Level Filter**: Added "Not Assigned" option to filter untagged questions
- **Session Persistence**: Filter and sort settings persist across page loads

#### Question Model Enhancements
- **Major Subtopic**: Assign a primary subtopic to each question
  - Separate from many-to-many subtopics
  - Must belong to the major topic (validation enforced)
- **Description Field**: Optional text description for questions
- **Enhanced Validation**: Ensures subtopic belongs to major topic when set

#### Document Generation Enhancements
- **Additional Answer Mode**: "All Questions, Then All Solutions"
- **Language Preference**: Prefer English or Chinese assets in generated documents
  - Automatic fallback to Bilingual if preferred not available
  - Format preference: IMG before DOC
- **Optional QID Display**: Separate toggles for showing QIDs on questions vs answers
- **Graceful Error Handling**: Missing assets show italic placeholder instead of crashing

#### Ingestor Enhancements
- **Automatic Question Type Detection**: 
  - MATC DSE P1 → CQ
  - MATC DSE P2 → MC
  - MAT1/MAT2 DSE → CQ
  - Others → NULL (to be tagged manually)
- **Natural Sorting**: Files are processed in natural order (Q1, Q2, Q10)

### 🐛 Bug Fixes
- Fixed language preference ordering in preview and generation
- Fixed page break logic to avoid duplicate breaks
- Improved asset selection with format preference (IMG over DOC)
- Enhanced error handling for missing files
- Fixed subtopic loading when topics are deselected

### 🔧 Technical Improvements
- Refactored sorting logic into reusable `apply_multi_sort()` function
- Added `SORT_FIELDS` configuration for extensible sort options
- Improved database query optimization for large datasets
- Enhanced session management for filter persistence
- Better separation of concerns in document generation
- Added comprehensive inline documentation

### 📚 Documentation Updates
- Updated README.md with all v2.0 features
- Updated SETUP.md with new CLI commands and features
- Updated PROJECT_SUMMARY.md with technical details
- Added this CHANGELOG.md to track version history
- Updated API endpoint documentation

### 🗄️ Database Changes
- Added `major_subtopic_id` column to questions table
- Added `description` column (TEXT) to questions table
- No migration needed for existing data (new columns allow NULL)

### 🔐 Security
- No changes to authentication or authorization
- Batch delete requires admin privileges
- Sync command requires file system access

---

## [1.0.0] - 2025-12-XX

### Initial Release

#### Core Features
- **Flask Application**: Complete web application structure
- **Database Layer**: SQLAlchemy ORM with MariaDB
  - 7 models: User, Subject, Topic, Subtopic, Question, QuestionAsset
  - 2 association tables: question_minor_topics, question_subtopics
- **Authentication**: Flask-Login with role-based access (Admin/Regular)
- **File Ingestor**: 
  - Recursive directory scanning
  - Regex-based filename parsing
  - Support for PP and QB formats
  - Error logging
- **Dashboard**:
  - Advanced filtering (subject, source, year, topic, level, type)
  - Cross-topic search
  - Question preview
  - Answer/solution preview modals
  - Pagination (20 per page)
- **Document Generator**:
  - Word document creation (python-docx)
  - A4 page size
  - Multiple answer modes
  - Configurable spacing
  - Natural sorting
- **Admin Panel**:
  - Topic/subtopic management (CRUD)
  - Question tagging
  - User registration
- **Multi-language Support**: EN, CH, BI
- **UI**: Bootstrap 5 + HTMX for dynamic interactions

#### Initial Documentation
- README.md - Project overview
- SETUP.md - Installation guide
- TESTING.md - Testing procedures
- PROJECT_SUMMARY.md - Technical summary

---

## Version Comparison

| Feature | v1.0.0 | v2.0.0 |
|---------|--------|--------|
| Multi-level Sorting | ❌ | ✅ |
| Batch Operations | ❌ | ✅ |
| Database Sync | ❌ | ✅ |
| Smart MC/CQ Spacing | ❌ | ✅ |
| QID Search | ❌ | ✅ |
| Topic AND/OR Mode | ❌ | ✅ |
| Language Preference | ❌ | ✅ |
| Major Subtopic | ❌ | ✅ |
| Question Description | ❌ | ✅ |
| Configurable Page Size | 20 only | 10/20/50/100 |
| Answer Modes | 4 | 5 |
| Auto Question Type | ❌ | ✅ |
| Session Persistence | ❌ | ✅ |

---

## Upgrade Guide: v1.0 → v2.0

### Database Migration

No manual migration required! The new columns (`major_subtopic_id`, `description`) allow NULL values, so existing data will work without changes.

If you want to explicitly add the columns (optional):

```sql
-- Only if not auto-created by SQLAlchemy
ALTER TABLE questions ADD COLUMN major_subtopic_id INT NULL;
ALTER TABLE questions ADD COLUMN description TEXT NULL;
ALTER TABLE questions ADD FOREIGN KEY (major_subtopic_id) REFERENCES subtopics(id);
```

### Configuration Changes

No `.env` changes required. All new features work with existing configuration.

### Feature Migration

1. **Sorting**: Old sort parameters are automatically converted to new format
2. **Filtering**: All old filters continue to work, new ones are optional
3. **Generation**: Old answer modes work as before, new modes available

### Testing After Upgrade

1. Test existing workflows (filtering, generation, tagging)
2. Try new features (batch operations, multi-level sort, QID search)
3. Run `python cli.py sync` (dry-run) to check for orphaned records
4. Verify language preferences work correctly

### Rollback

If needed, you can roll back by:
1. Restore database backup
2. Checkout v1.0.0 code
3. Restart application

---

## Roadmap

### Future Enhancements (v2.1)
- Export/Import question sets
- Question statistics and analytics
- Advanced search with full-text search
- Question versioning/history
- Collaborative tagging with change tracking
- Mobile app integration

### Future Enhancements (v3.0)
- AI-powered question tagging suggestions
- Automatic difficulty level detection
- Question similarity detection
- LaTeX/MathML support for equations
- Multi-user collaborative editing
- Real-time notifications

---

## Support

For issues or questions about any version:
- Check documentation: README.md, SETUP.md, TESTING.md
- Review error logs: Terminal output, `ingest_errors.log`
- Check database: phpMyAdmin or MySQL client

## Contributors

Developed and maintained by the OQB Team.

---

**Last Updated**: January 9, 2026
