# ADR-017 — Bilingual UI and taxonomy (English / Traditional Chinese)

**Status:** accepted, in force since 2026-10-17. Phase 1 (the schema, both language switches, bilingual taxonomy display and matching, document heading language, navbar / login / Toolbox hub) and Phase 2 (every remaining page, admin pages included, plus route JSON messages) are both live (see [STATUS.md](../../STATUS.md)).

## Context

The only Chinese in OQB was the paper **version** (`EN / CH / BI / ENO / CHO`, [ADR-006](ADR-006-versions-replace-languages.md)): which file of a question to print. The interface was English only, and the taxonomy had one `name` column, so CHEM packed both languages into it (`Planet Earth 地球`). That string was what Auto Tag had to echo back, what CSV import matched on, and what every heading printed, even on an English paper.

Options considered:

1. **Flask-Babel / gettext.** Standard, but needs `.po` / `.mo` files, a compile step and a JS bridge for the many inline scripts. Too heavy for a server-rendered app with no build step ([ADR-001](ADR-001-server-rendered-htmx-no-spa.md)).
2. **A user-profile language column.** Needs a schema change and follows the person, while teachers here share classroom machines and switch per device.
3. **Separate `name_en` / `name_zh` columns, renaming `name`.** Every query, sort, CSV and restore point uses `name`; renaming it is a large risky migration for no gain.
4. **An in-house JSON catalogue keyed by the English text, two cookies, and an optional `name_zh` beside `name`.** Chosen.

## Decision

- **Data:** `name` stays the English name (sort key, unique-in-practice match key, what every existing tool reads). `name_zh` (nullable) is added to `subjects`, `topics`, `subtopics`, `chapters`, `subchapters` by a boot patch. It is part of the restore-point payload; points taken before 2026-10-17 lack it and leave the live value alone.
- **UI language:** per-device cookie `oqb_lang` (`en` | `zh`), set from the navbar Language menu. No DB column. Strings go through `t()` in Jinja / Python and `OQB.t()` in JS, both reading `app/translations/zh_Hant.json` keyed by the exact English text. A missing key shows the English. The JS catalogue is served by `GET /i18n/zh.js` (no login, cache-busted by mtime).
- **Taxonomy display:** a second cookie `oqb_tax_lang` (`en` | `zh` | `both`, default `en`), independent of the UI language, through `i18n.display_name` / `OQB.taxName`. Chinese falls back to English when blank.
- **Paper language** (amended 2026-10-17): a third cookie `oqb_paper_lang` (`en` | `zh` | `bi`, unset = no preference), set in the same menu and independent of the other two. It only seeds the **default** version priority (`i18n.paper_version_priority` → `OQB_DEFAULT_VERSION_PRIORITY`): English = EN, BI, ENO, CH, CHO; 中文 = CH, BI, CHO, EN, ENO; Bilingual = BI first. Anything explicit still wins: the dashboard Version Priority widget after a manual drag, a loaded preset, regenerate options. It is a per-device default rather than a user column for the same reason as `oqb_lang`.
- **Documents:** never read a cookie. The Generate page's **Heading language** (`auto` | `en` | `zh` | `both`, saved in presets) resolves through `i18n.doc_lang`: `auto` follows the top paper version (EN/ENO → English, CH/CHO → 中文, BI → both). It covers section headings, the info line, split file names, the ANSWERS / SOLUTIONS title and the Cross Topic note. Grouping keys are ids, so the language cannot change how questions are grouped.
- **Matching accepts every form:** Auto Tag replies, question-tag CSV import and topic / chapter CSV import match English, Chinese, or `English 中文` (`i18n.build_name_index`). Stored references are always ids; the tag-correction log keeps the English name.
- **Translation scope:** teacher-facing text, including admin pages. Progress logs, SSE lines and AI prompts stay English. Translations are Hong Kong Traditional Chinese drafted by the agent, with a fixed term list ([../reference/glossary-zh.md](../reference/glossary-zh.md)).

## Consequences

- Adding a UI string means wrapping it in `t()` / `OQB.t()` **and** adding the key to `zh_Hant.json`; `tests/test_i18n.py` fails on any literal without a translation. Template literals and concatenations are not scanned, so catalogue strings must be single plain literals.
- Changing English wording changes the key: the old Chinese no longer applies until the catalogue is updated (the test catches literals, not stale keys).
- UI language and paper version are two different things. Never derive one from the other, except `heading_lang=auto`. The paper-language cookie is set explicitly; it is never inferred from the UI language.
- With no preset loaded, a device on paper language 中文 starts Generate with CH first, so `heading_lang=auto` gives Chinese headings there.
- A subject without Chinese names behaves exactly as before: the Auto Tag prompt only gains the `[中: …]` alias note when a visible node has a Chinese name.
- CHEM's packed names must be split by hand (English into Name, Chinese into the Chinese name), and its tag-correction log cleared, because old corrections quote the packed names.

Details: [../modules/i18n.md](../modules/i18n.md). Related: [ADR-006](ADR-006-versions-replace-languages.md) (versions), [ADR-012](ADR-012-subject-prompt-layer-over-fine-tuning.md) (Auto Tag prompt layer), [ADR-013](ADR-013-subject-restore-points.md) (restore points).
