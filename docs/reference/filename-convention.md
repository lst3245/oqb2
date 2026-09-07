# Reference — QID and asset filename convention

> Domain facts, not architecture. The ingestor (`app/ingestor.py`) and every upload route depend on this grammar; the on-disk layout under `SOURCE_PATH` mirrors it. Changing any token here is a data migration, not a code tweak.

## Question ID (QID)

| Kind | Grammar | Example |
|---|---|---|
| Past paper | `SUBJ_SOURCE_YEAR_PAPER_QNO` | `MATC_DSE_2024_P1_Q5` |
| Question bank | `SUBJ_QB_DETAIL_QNO` | `MATC_QB_MATHSMART2024_Q1` |

`Question.qid` is unique. `Question.subject = SUBJ`, `source ∈ DSE | CE | AL | QB`, `year` int (NULL for QB), `paper` (e.g. `P1`, `P2`), `qno` = the integer after `Q`. `DETAIL` for QB has no underscores.

## Asset filename

```
<QID>_<VERSION>_<TYPE>[_<PART>].<EXT>
MATC_DSE_2024_P1_Q5_EN_QUE.png        image, part 1
MATC_DSE_2024_P1_Q5_EN_QUE_2.png      image, part 2 (multi-image question)
MATC_DSE_2024_P1_Q5_ENO_QUE.png       official English public-exam scan
MATC_DSE_2024_P1_Q5_EN_SOL.docx       Word solution (single slot)
MATC_QB_MATHSMART2024_Q1_CH_QUE.md    Markdown question (single slot)
```

| Token | Values | Notes |
|---|---|---|
| `SUBJ` | any `Subject.id` (`MATC`, `MAT1`, `MAT2`, `ICT`, `ECON`, ...) | `\w+` in the regex; must exist in `subjects` |
| `SOURCE` | `DSE`, `CE`, `AL` (past paper) or `QB` | |
| `YEAR` | digits | past paper only |
| `PAPER` | `P` + alphanumerics | `P1`, `P2`, `P1A` |
| `QNO` | `Q` + digits | |
| `VERSION` | `EN`, `CH`, `BI`, `ENO`, `CHO` | the regex alternation lists `ENO|CHO` **before** `EN|CH` so the longer tokens are not shadowed. `ENO`/`CHO` = official scans (reference for proofreading, last in default priority) |
| `TYPE` | `QUE`, `ANS`, `SOL` | |
| `PART` | integer ≥ 2 | optional; only meaningful for IMG. `.md` with a part ≥ 2 is skipped by ingest; DOC is single-slot |
| `EXT` | `png jpg jpeg gif bmp` → `IMG`; `doc docx` → `DOC`; `md markdown` → `MD` | `determine_file_format` |

Regexes: `PP_PATTERN` and `QB_PATTERN` in `app/ingestor.py`; `parse_filename()` tries PP then QB and returns the group dict with `part` defaulting to 1. `construct_qid(parsed)` rebuilds the QID.

## Folder layout under `SOURCE_PATH`

```
<SOURCE_PATH>/
  <SUBJ>/
    PP/<SOURCE>/<YEAR>/<PAPER>/   MATC/PP/DSE/2024/P1/MATC_DSE_2024_P1_Q5_EN_QUE.png
    QB/<DETAIL>/                  MATC/QB/MATHSMART2024/MATC_QB_MATHSMART2024_Q1_EN_QUE.png
```

`QuestionAsset.file_path` is stored **relative to `SOURCE_PATH` with forward slashes**, including the subject prefix (`MATC/PP/DSE/2024/P1/...`). When scanning a subfolder pass `base_path=SOURCE_PATH` so the prefix is preserved.

## Auto question type at ingest (`determine_question_type`)

| Subject / source / paper | `q_type` |
|---|---|
| `MATC DSE P1` | `CQ` |
| `MATC DSE P2` | `MC` |
| `MAT1` or `MAT2` `DSE` | `CQ` |
| anything else | `NULL` (tag manually) |

## Related invariants

- Unique asset identity: `(question_id, asset_type, version, file_format, part_number)`.
- Renaming a QID (Admin → Edit modal → Details) renames every asset file on disk and updates `file_path`; do not rename files by hand.
- Subject ids are immutable because they are embedded in every QID and folder path.
- Ingest is additive; `cli.py sync --no-dry-run` removes DB rows whose files vanished (24-hour grace on newly created questions). See [../modules/ingestion.md](../modules/ingestion.md).
