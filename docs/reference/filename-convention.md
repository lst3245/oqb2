# Reference — QID and asset filename convention

> Domain facts, not architecture. The ingestor (`app/ingestor.py`) and every upload route depend on this grammar; the on-disk layout under `SOURCE_PATH` mirrors it. Changing any token here is a data migration, not a code tweak.

## Question ID (QID)

| Kind | Grammar | Example |
|---|---|---|
| Past paper | `SUBJ_SOURCE_YEAR_PAPER_QNO` | `MATC_DSE_2024_P1_Q5`, `ECON_DSE_2023_P1_Q23-24`, `ICT_DSE_2024_P1_Q3a` |
| Question bank | `SUBJ_QB_DETAIL_QNO` | `MATC_QB_MATHSMART2024_Q1` |

`Question.qid` is unique. `Question.subject = SUBJ`, `source ∈ DSE | CE | AL | QB`, `year` int (NULL for QB), `paper` (e.g. `P1`, `P2`). `DETAIL` for QB has no underscores.

`QNO` is a **token**, not only digits. Canonical forms (source of truth: `app/hierarchy.py`):

| Token | Meaning | Stored |
|---|---|---|
| `Q5` | Standalone / stem of a structured question | `qno=5`, `qno_end=NULL`, `part=NULL` |
| `Q5a` | Letter part under `Q5` | `qno=5`, `part=a` |
| `Q3ci` | Roman sub-part under `Q3c` | `qno=3`, `part=i` (own label; parent is `Q3c`) |
| `Q23-24` | Range stem (shared MC preamble) | `qno=23`, `qno_end=24`. Mutually exclusive with a part-path |

`qno` is always the integer **start**. `(subject, source, year, paper, qno)` is not unique. Tree behaviour: [../modules/question-hierarchy.md](../modules/question-hierarchy.md).

## Asset filename

```
<QID>_<VERSION>_<TYPE>[_<PART>].<EXT>
MATC_DSE_2024_P1_Q5_EN_QUE.png        image, IMG part 1 of Q5
MATC_DSE_2024_P1_Q5_EN_QUE_2.png      image, IMG part 2 (second page of the same QID)
MATC_DSE_2024_P1_Q5_EN_WHOLE.png      unsplit original of root Q5 (archive; not used in papers)
MATC_DSE_2024_P1_Q5_EN_WHOLE_2.png    second page of that archive
ECON_DSE_2023_P1_Q3a_ENO_QUE.png      image of sub-question Q3a (different QID)
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
| `QNO` | `Q` + digits, optional `-` + digits, optional lowercase part-path | `Q5`, `Q5a`, `Q3ci`, `Q23-24`. Pattern `QNO_TOKEN_PATTERN` in `app/hierarchy.py`. Range and part-path are mutually exclusive |
| `VERSION` | `EN`, `CH`, `BI`, `ENO`, `CHO` | the regex alternation lists `ENO|CHO` **before** `EN|CH` so the longer tokens are not shadowed. `ENO`/`CHO` = official scans (reference for proofreading, last in default priority) |
| `TYPE` | `QUE`, `ANS`, `SOL`, `WHOLE` | `WHOLE` is IMG-only on a **root** QID (`Q5`, not `Q5a` / `Q23-24`). Ingest parses a part-QID WHOLE filename then skips it. Dashboard / generator / viewer never load it. |
| `PART` | integer ≥ 2 | optional; **IMG page N of this QID** (`QuestionAsset.part_number`). Not the sub-question letter. `.md` with a part ≥ 2 is skipped by ingest; DOC is single-slot |
| `EXT` | `png jpg jpeg gif bmp` → `IMG`; `doc docx` → `DOC`; `md markdown` → `MD` | `determine_file_format` |

Regexes: `PP_PATTERN` and `QB_PATTERN` in `app/ingestor.py` embed `QNO_TOKEN_PATTERN`. `parse_filename()` tries PP then QB and returns the group dict with `part` defaulting to 1 (**IMG page**, not the question `part` column) and `qno` as the full token string (`Q5a`). `construct_qid(parsed)` rebuilds the QID from that token.

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
- `WHOLE` filenames (`..._EN_WHOLE.png`) are root-only. A `..._Q3a_EN_WHOLE.png` parses then ingest skips it.
- Renaming a QID (Admin → Edit modal → Details) rewrites that question and its descendants (QIDs + optional files) and updates `file_path`; do not rename files by hand.
- `..._QUE_2.png` and `..._Q3a_QUE.png` are different axes. Do not invent a third QNO regex.
- Subject ids are immutable because they are embedded in every QID and folder path.
- Ingest is additive; `cli.py sync --no-dry-run` removes DB rows whose files vanished (24-hour grace on newly created questions). See [../modules/ingestion.md](../modules/ingestion.md).
