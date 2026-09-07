# Reference — seeded and static data

> Facts about data the repo ships or seeds. Not architecture.

## Seeded by `init_db.py` (fresh, empty database only)

Runs `db.create_all()` and then, **only if `subjects` is empty**, inserts:

| Kind | Values |
|---|---|
| Subjects | `MATC` Mathematics Compulsory Part · `MAT1` Mathematics Module 1 (Calculus and Statistics) · `MAT2` Mathematics Module 2 (Algebra and Calculus) · `ICT` Information and Communication Technology |
| Admin user | `admin` / `admin123` (`is_admin=True`; note `is_super_admin` is **not** set by the script — promote via DB or an existing super admin) |
| Sample MATC topics | Number and Algebra (Polynomials, Equations, Inequalities) · Calculus (Differentiation, Integration, Applications) · Probability (Basic, Conditional, Distributions) · Statistics (Descriptive, Inferential) |

The live deployment has more subjects (e.g. `ECON`) created through Admin → Manage Subjects. Do not assume the seed list is the current list; query `subjects`.

## Seeded at every boot (idempotent)

- `prompt_variants`: one built-in row per key in `app/ai_prompts.PROMPTS_REGISTRY` (`content=NULL` = registry default), plus migration of any legacy `prompt_overrides` content on first run. See [../modules/ai-prompts.md](../modules/ai-prompts.md).
- Storage tree directories `Shared/ System/ User/` ([../core/05-storage-and-paths.md](../core/05-storage-and-paths.md)).

## Static resources in the repo

| Path | Used by |
|---|---|
| `resources/mcq_answer_img/A.png`, `B.png`, `C.png`, `D.png` | Admin → Question Management → **Set MCQ ANS** batch op copies the matching letter PNG into the ANS slot of MC questions ([../modules/admin-questions.md](../modules/admin-questions.md)) |
| `static/img/` | logos and favicon SVGs (`base.html`, `login.html`) |
| `static/markup/manifest.webmanifest`, `sw.js`, `icon.svg` | Markup PWA assets served root-scoped by `app/pwa.py` ([../modules/markup.md](../modules/markup.md)) |
| `static/css/`, `static/js/` | empty placeholders (`.gitkeep`); page JS lives inline in templates and `base.html` |

## Domain vocabulary

| Term | Meaning |
|---|---|
| DSE / CE / AL | Hong Kong public exams: HKDSE, HKCEE, HKALE (`Question.source`) |
| QB | question bank (non-exam source); `DETAIL` names the bank |
| MC / CQ | multiple choice / conventional (long) question (`q_type`) |
| Level 1–3 | difficulty tag set manually |
| Version EN / CH / BI / ENO / CHO | English, Chinese, bilingual typed versions; English / Chinese **official** scans |
| QUE / ANS / SOL | question, short answer, full solution asset types |
| Section | free-text paper section (`A`, `B`, `Section I`) |
| Correct percentage | published public-exam correct rate for the question |
| Verified | admin has confirmed assets and tagging for the whole question |
