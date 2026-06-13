"""
Prompt templates + output parsing for the AI features.

The prompt content (system + user-turn + output-format templates for
proofreading, MD generation, the Explain tutor, the figure-bbox detector,
and the PDF batch-import bbox detector) lives in PROMPTS_REGISTRY below as
bootstrap defaults, and is overridable at runtime via the Admin -> AI
Prompts page (super-admin only). Each key supports MULTIPLE named variants
(table `prompt_variants`, see app/models.py PromptVariant): one editable
built-in row (NULL content = bootstrap default) plus admin-created customs,
with one variant marked active per key. LLM endpoints may be pinned to a
specific variant per key (`prompt_endpoint_assignments`); unpinned
endpoints use the active variant.

Output-formatting rules are factored into separate *_FORMAT items (declared
via the spec's ``format_key``) and are injected into BOTH turns: appended
to the system prompt by ``system_prompt()`` and re-appended at the end of
the user turn by ``append_format()`` / the build_*_user_text helpers —
some models (notably GPT builds) under-weight system-role formatting rules.

Variable substitution syntax for prompts that take parameters:
  ``{{var}}``  - replaced with the value of ``var``.
Single ``{`` / ``}`` are LITERAL - they pass through unchanged. This
matters because several prompts contain JSON examples like ``{"status":
"ok"}`` that we must not mangle. Use double-braces only for our own
substitution points.

Output parsing (parse_check_result, parse_figure_boxes,
parse_question_boxes, normalize_inline_math, strip_md_fences, the figure
regex) lives at the bottom of this module unchanged - those are pure
utility functions, not prompts.
"""
import json
import logging
import re
from collections import OrderedDict
from threading import Lock
from typing import Any


logger = logging.getLogger(__name__)


# ==================== Prompt registry ========================================

class _PromptSpec(dict):
    """Metadata for one editable prompt. Behaves like a dict so it serialises
    cleanly in the /admin/prompts/data response."""
    __getattr__ = dict.__getitem__


def _prompt(*, group, label, description, default,
            variables=None, role='system', format_key=None):
    return _PromptSpec(
        group=group,
        label=label,
        description=description,
        default=default,
        variables=list(variables or []),
        role=role,
        format_key=format_key,
    )


# ---- Resolver / cache ------------------------------------------------------
#
# Module-level cache so chat calls don't hit the DB every time. Cache keys
# are ``(prompt_key, endpoint_id_or_None)`` because endpoints may be pinned
# to a specific variant. Any variant/assignment write invalidates; reads
# repopulate. Single-process assumption — same caveat as the system
# settings cache.

_PROMPT_CACHE: dict = {}
_CACHE_LOCK = Lock()

MAX_PROMPT_CHARS = 32000


def _load_resolved(key, endpoint_id=None):
    """Resolve the live content for ``key`` from the variant tables.

    Resolution order:
      1. the variant explicitly assigned to ``endpoint_id`` for this key;
      2. else the key's active variant;
      3. ``None`` when the winning variant has no content (built-in row with
         NULL content) or the DB isn't reachable — caller falls back to the
         registry bootstrap default.
    """
    try:
        from app.models import PromptVariant, PromptEndpointAssignment
        variant = None
        if endpoint_id:
            row = (PromptEndpointAssignment.query
                   .filter_by(prompt_key=key, endpoint_id=endpoint_id)
                   .first())
            if row is not None:
                variant = PromptVariant.query.get(row.variant_id)
        if variant is None:
            variant = (PromptVariant.query
                       .filter_by(prompt_key=key, is_active=True)
                       .first())
        if variant is not None and (variant.content or '').strip():
            return variant.content
        return None
    except Exception as e:  # pragma: no cover — DB down / pre-init
        logger.debug('Prompt variant load failed for %s: %s', key, e)
        return None


def get_prompt(key: str, endpoint_id=None) -> str:
    """Return the live content for ``key``, honouring a per-endpoint variant
    assignment when ``endpoint_id`` is given (else the key's active variant,
    else the bootstrap default). Raises KeyError on an unknown key."""
    if key not in PROMPTS_REGISTRY:
        raise KeyError(f'Unknown prompt key: {key}')

    cache_key = (key, endpoint_id)
    with _CACHE_LOCK:
        cached = _PROMPT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    resolved = _load_resolved(key, endpoint_id)
    content = resolved if resolved is not None else PROMPTS_REGISTRY[key]['default']

    with _CACHE_LOCK:
        _PROMPT_CACHE[cache_key] = content
    return content


# Variable substitution: only `{{name}}` is replaced — single `{` / `}` are
# left alone so JSON examples and other curly-brace literals in prompts pass
# through untouched. Unknown names render as `{{name}}` literally so the
# model's reply makes the misuse visible at debug time.
_VAR_RE = re.compile(r'\{\{(\w+)\}\}')


def render_prompt(key: str, endpoint_id=None, **vars: Any) -> str:
    """Return ``get_prompt(key, endpoint_id)`` with declared ``{{var}}``
    placeholders substituted. Variables not declared in the registry for
    that key are ignored. Missing declared variables leave the literal
    ``{{var}}`` in the output (so prompt-design errors surface in the
    model's reply rather than crashing the request)."""
    template = get_prompt(key, endpoint_id)
    if not vars:
        return template

    spec = PROMPTS_REGISTRY[key]
    declared = set(spec['variables'])
    if not declared:
        return template

    def _sub(m):
        name = m.group(1)
        if name in declared and name in vars:
            return str(vars[name])
        return m.group(0)

    return _VAR_RE.sub(_sub, template)


# ---- Format-block composition ----------------------------------------------
#
# Prompts whose spec declares ``format_key`` get their output-formatting
# rules injected from a SEPARATE registry item: appended to the system
# prompt AND re-appended at the end of the user turn so models that
# under-weight the system role (several GPT builds) still see the contract
# at task time.

FORMAT_EMPHASIS_HEADER = 'OUTPUT FORMAT (mandatory):'


def format_block(key: str, endpoint_id=None, **vars: Any) -> str:
    """Resolved formatting-rules block for ``key`` (via its ``format_key``),
    or '' when the key declares none."""
    spec = PROMPTS_REGISTRY.get(key) or {}
    fkey = spec.get('format_key')
    if not fkey:
        return ''
    return render_prompt(fkey, endpoint_id=endpoint_id, **vars)


def system_prompt(key: str, endpoint_id=None, **vars: Any) -> str:
    """Full system prompt for ``key``: the base prompt plus its formatting
    block (when one is declared). Use this at call sites instead of
    ``get_prompt`` for the *_SYSTEM prompts that carry a ``format_key``."""
    base = render_prompt(key, endpoint_id=endpoint_id, **vars)
    fmt = format_block(key, endpoint_id=endpoint_id, **vars)
    return f'{base}\n\n{fmt}' if fmt else base


def append_format(key: str, text: str, endpoint_id=None, **vars: Any) -> str:
    """Append ``key``'s formatting block (if any) to a user-turn ``text``
    under an emphasis header. Returns ``text`` unchanged when the key has
    no ``format_key``."""
    fmt = format_block(key, endpoint_id=endpoint_id, **vars)
    if not fmt:
        return text
    return f'{text}\n\n{FORMAT_EMPHASIS_HEADER}\n{fmt}'


# ---- Variant CRUD + endpoint assignment -------------------------------------

def _validate_content(content) -> str:
    """Normalise + validate prompt content. Returns the cleaned string."""
    if not isinstance(content, str):
        raise ValueError('content must be a string')
    content = content.strip('\ufeff').rstrip()  # strip BOM + trailing whitespace
    if not content:
        raise ValueError('content must not be empty')
    if len(content) > MAX_PROMPT_CHARS:
        raise ValueError(f'content exceeds {MAX_PROMPT_CHARS} characters')
    return content


BUILTIN_VARIANT_NAME = 'Built-in default'


def _ensure_builtin(key):
    """Return the built-in variant row for ``key``, creating it (uncommitted)
    when missing. Caller commits."""
    from app import db
    from app.models import PromptVariant

    row = PromptVariant.query.filter_by(prompt_key=key, is_builtin=True).first()
    if row is None:
        has_active = (PromptVariant.query
                      .filter_by(prompt_key=key, is_active=True).first() is not None)
        row = PromptVariant(prompt_key=key, name=BUILTIN_VARIANT_NAME,
                            content=None, is_builtin=True,
                            is_active=not has_active, sort_order=0)
        db.session.add(row)
    return row


def ensure_seeded() -> None:
    """Idempotent startup seed: one built-in variant per registry key, with
    any legacy ``prompt_overrides`` row migrated into the built-in row's
    content the FIRST time it is created. Existing rows are never touched,
    so admin edits survive restarts. Also guarantees one active variant per
    key. Swallows DB errors (pre-init boot)."""
    from app import db
    from app.models import PromptVariant, PromptOverride

    try:
        legacy = {}
        try:
            for row in PromptOverride.query.all():
                legacy[row.key] = row.content
        except Exception:
            legacy = {}

        changed = False
        for key in PROMPTS_REGISTRY:
            builtin = (PromptVariant.query
                       .filter_by(prompt_key=key, is_builtin=True).first())
            if builtin is None:
                has_active = (PromptVariant.query
                              .filter_by(prompt_key=key, is_active=True)
                              .first() is not None)
                builtin = PromptVariant(
                    prompt_key=key, name=BUILTIN_VARIANT_NAME,
                    content=legacy.get(key), is_builtin=True,
                    is_active=not has_active, sort_order=0)
                db.session.add(builtin)
                changed = True
            else:
                # Guarantee one active variant per key.
                active = (PromptVariant.query
                          .filter_by(prompt_key=key, is_active=True).first())
                if active is None:
                    builtin.is_active = True
                    changed = True
        if changed:
            db.session.commit()
            invalidate_cache()
    except Exception as e:  # pragma: no cover — DB down / pre-init
        try:
            from app import db as _db
            _db.session.rollback()
        except Exception:
            pass
        logger.debug('Prompt variant seed skipped: %s', e)


def list_variants(key):
    """All variants for ``key`` (built-in first, then by sort_order, name)."""
    from app.models import PromptVariant
    return (PromptVariant.query.filter_by(prompt_key=key)
            .order_by(PromptVariant.is_builtin.desc(),
                      PromptVariant.sort_order, PromptVariant.name)
            .all())


def create_variant(key: str, name: str, content: str, user_id=None):
    """Create a custom variant for ``key``. Returns the new row."""
    if key not in PROMPTS_REGISTRY:
        raise KeyError(f'Unknown prompt key: {key}')
    name = (name or '').strip()
    if not name:
        raise ValueError('name must not be empty')
    if len(name) > 120:
        raise ValueError('name exceeds 120 characters')
    content = _validate_content(content)

    from app import db
    from app.models import PromptVariant

    if PromptVariant.query.filter_by(prompt_key=key, name=name).first():
        raise ValueError(f'A variant named "{name}" already exists for this prompt')

    _ensure_builtin(key)
    max_sort = (db.session.query(db.func.max(PromptVariant.sort_order))
                .filter_by(prompt_key=key).scalar()) or 0
    row = PromptVariant(prompt_key=key, name=name, content=content,
                        is_builtin=False, is_active=False,
                        sort_order=max_sort + 1, updated_by=user_id)
    db.session.add(row)
    db.session.commit()
    invalidate_cache()
    logger.info('PromptVariant created: %s/%s (by user %s)', key, name, user_id)
    return row


def update_variant(variant_id: int, name=None, content=None, user_id=None):
    """Edit a variant. The built-in variant accepts content edits only (its
    name is fixed). Returns the row."""
    from app import db
    from app.models import PromptVariant

    row = PromptVariant.query.get(variant_id)
    if row is None:
        raise KeyError('Variant not found')

    if name is not None and not row.is_builtin:
        name = (name or '').strip()
        if not name:
            raise ValueError('name must not be empty')
        if len(name) > 120:
            raise ValueError('name exceeds 120 characters')
        clash = (PromptVariant.query
                 .filter(PromptVariant.prompt_key == row.prompt_key,
                         PromptVariant.name == name,
                         PromptVariant.id != row.id)
                 .first())
        if clash:
            raise ValueError(f'A variant named "{name}" already exists for this prompt')
        row.name = name

    if content is not None:
        row.content = _validate_content(content)

    row.updated_by = user_id
    db.session.commit()
    invalidate_cache()
    logger.info('PromptVariant updated: %s/%s (by user %s)',
                row.prompt_key, row.name, user_id)
    return row


def delete_variant(variant_id: int) -> None:
    """Delete a custom variant. Its endpoint assignments are removed; if it
    was the active variant, the built-in becomes active. Deleting the
    built-in is rejected."""
    from app import db
    from app.models import PromptVariant, PromptEndpointAssignment

    row = PromptVariant.query.get(variant_id)
    if row is None:
        raise KeyError('Variant not found')
    if row.is_builtin:
        raise ValueError('The built-in variant cannot be deleted (reset it instead)')

    key = row.prompt_key
    was_active = row.is_active
    (PromptEndpointAssignment.query
     .filter_by(variant_id=row.id).delete(synchronize_session=False))
    db.session.delete(row)
    if was_active:
        builtin = _ensure_builtin(key)
        builtin.is_active = True
    db.session.commit()
    invalidate_cache()
    logger.info('PromptVariant deleted: %s/%s', key, row.name)


def set_active(variant_id: int):
    """Make ``variant_id`` the active (default) variant for its key."""
    from app import db
    from app.models import PromptVariant

    row = PromptVariant.query.get(variant_id)
    if row is None:
        raise KeyError('Variant not found')
    (PromptVariant.query
     .filter(PromptVariant.prompt_key == row.prompt_key,
             PromptVariant.id != row.id)
     .update({'is_active': False}, synchronize_session=False))
    row.is_active = True
    db.session.commit()
    invalidate_cache()
    return row


def reset_builtin(key: str, user_id=None) -> str:
    """Null the built-in variant's content so the bootstrap default applies
    again. Returns the default content."""
    if key not in PROMPTS_REGISTRY:
        raise KeyError(f'Unknown prompt key: {key}')

    from app import db

    builtin = _ensure_builtin(key)
    builtin.content = None
    builtin.updated_by = user_id
    db.session.commit()
    invalidate_cache()
    logger.info('Prompt built-in reset to bootstrap default: %s (by user %s)',
                key, user_id)
    return PROMPTS_REGISTRY[key]['default']


def set_variant_endpoints(variant_id: int, endpoint_ids) -> None:
    """Declaratively set which endpoints are pinned to ``variant_id`` for its
    key. Endpoints previously pinned to this variant but absent from
    ``endpoint_ids`` lose their assignment (falling back to the active
    variant); endpoints pinned to a sibling variant are re-pointed here."""
    from app import db
    from app.models import PromptVariant, PromptEndpointAssignment, LLMConfig

    row = PromptVariant.query.get(variant_id)
    if row is None:
        raise KeyError('Variant not found')
    key = row.prompt_key

    wanted = set()
    for eid in (endpoint_ids or []):
        try:
            wanted.add(int(eid))
        except (TypeError, ValueError):
            continue
    valid = {c.id for c in LLMConfig.query.filter(LLMConfig.id.in_(wanted)).all()} \
        if wanted else set()

    existing = PromptEndpointAssignment.query.filter_by(prompt_key=key).all()
    for a in existing:
        if a.variant_id == row.id and a.endpoint_id not in valid:
            db.session.delete(a)
        elif a.endpoint_id in valid and a.variant_id != row.id:
            a.variant_id = row.id
            valid.discard(a.endpoint_id)
        elif a.variant_id == row.id:
            valid.discard(a.endpoint_id)
    for eid in valid:
        db.session.add(PromptEndpointAssignment(
            prompt_key=key, endpoint_id=eid, variant_id=row.id))
    db.session.commit()
    invalidate_cache()


def unassign_endpoint(key: str, endpoint_id: int) -> None:
    """Remove ``endpoint_id``'s explicit assignment for ``key`` (it falls
    back to the active variant)."""
    from app import db
    from app.models import PromptEndpointAssignment

    (PromptEndpointAssignment.query
     .filter_by(prompt_key=key, endpoint_id=int(endpoint_id))
     .delete(synchronize_session=False))
    db.session.commit()
    invalidate_cache()


def invalidate_cache(key=None) -> None:
    """Drop every cached entry for one prompt key (any endpoint), or the
    whole cache when ``key`` is None. Called automatically by the variant /
    assignment writers; also exposed so tests / live-reloaders can force a
    refresh."""
    with _CACHE_LOCK:
        if key is None:
            _PROMPT_CACHE.clear()
        else:
            for ck in [ck for ck in _PROMPT_CACHE if ck[0] == key]:
                _PROMPT_CACHE.pop(ck, None)


def as_dict() -> dict:
    """Serialise the registry + variants + endpoint assignments for the
    admin UI."""
    from app.models import PromptVariant, PromptEndpointAssignment, LLMConfig

    variants_by_key: dict = {}
    assigns_by_variant: dict = {}
    endpoints = []
    try:
        for v in (PromptVariant.query
                  .order_by(PromptVariant.is_builtin.desc(),
                            PromptVariant.sort_order, PromptVariant.name)
                  .all()):
            variants_by_key.setdefault(v.prompt_key, []).append(v)
        for a in PromptEndpointAssignment.query.all():
            assigns_by_variant.setdefault(a.variant_id, []).append(a.endpoint_id)
        endpoints = [
            {'id': c.id, 'name': c.name, 'enabled': bool(c.enabled)}
            for c in LLMConfig.query.order_by(LLMConfig.sort_order, LLMConfig.name).all()
        ]
    except Exception as e:  # pragma: no cover — pre-init / DB down
        logger.debug('Prompt variant query failed: %s', e)

    out_registry = OrderedDict()
    groups = []
    for key, spec in PROMPTS_REGISTRY.items():
        if spec['group'] not in groups:
            groups.append(spec['group'])

        variants = []
        for v in variants_by_key.get(key, []):
            assigned = sorted(assigns_by_variant.get(v.id, []))
            variants.append({
                'id': v.id,
                'name': v.name,
                'is_builtin': bool(v.is_builtin),
                'is_active': bool(v.is_active),
                'content': (v.content if (v.content or '').strip()
                            else (spec['default'] if v.is_builtin else '')),
                'has_custom_content': bool((v.content or '').strip()),
                'assigned_endpoint_ids': assigned,
                'assigned_count': len(assigned),
                'updated_at': v.updated_at.isoformat() if v.updated_at else None,
                'updated_by_username': (
                    v.updated_by_user.username if v.updated_by_user else None),
            })
        if not variants:
            # Pre-seed DB state: synthesise the built-in so the UI still works.
            variants.append({
                'id': None,
                'name': BUILTIN_VARIANT_NAME,
                'is_builtin': True,
                'is_active': True,
                'content': spec['default'],
                'has_custom_content': False,
                'assigned_endpoint_ids': [],
                'assigned_count': 0,
                'updated_at': None,
                'updated_by_username': None,
            })

        out_registry[key] = {
            'key': key,
            'group': spec['group'],
            'label': spec['label'],
            'description': spec['description'],
            'variables': spec['variables'],
            'role': spec['role'],
            'format_key': spec.get('format_key'),
            'default': spec['default'],
            'variants': variants,
        }
    return {'groups': groups, 'registry': out_registry, 'endpoints': endpoints}


# ==================== Image checking (proofreading) ====================

_DEFAULT_CHECK_SYSTEM = (
    "You are a meticulous bilingual (English/Chinese) exam-paper proofreader. "
    "You are given two images of the SAME exam question asset: an OFFICIAL "
    "scanned version (the ground truth) and a TYPED reproduction that may "
    "contain transcription mistakes. Compare them carefully and report every "
    "discrepancy in the TYPED version relative to the OFFICIAL one: typos, "
    "wrong or missing numbers, altered mathematical symbols/expressions, "
    "missing or extra words, wrong subscripts/superscripts, swapped options, "
    "and formatting errors that change meaning. Ignore differences that do "
    "not affect meaning (font, colour, resolution, scan artefacts, layout, "
    "page margins, watermarks)."
)


# Output-format contract for proofreading. Parsed by parse_check_result —
# keep the JSON shape in sync with the parser.
_DEFAULT_CHECK_FORMAT = (
    "Respond with STRICT JSON only (no prose, no markdown fences) of the form:\n"
    '{"status": "ok"} when the typed version is faithful, OR\n'
    '{"status": "issues", "issues": [{"location": "<where>", '
    '"description": "<what is wrong>", "severity": "minor|major"}]}\n'
    "Use \"major\" when the mistake changes the meaning or the answer."
)


_DEFAULT_CHECK_USER = (
    "Asset type: {{asset_type}}. The FIRST image(s) are the OFFICIAL scanned "
    "version ({{ref_version}}). The following image(s) are the TYPED version "
    "({{typed_version}}) to be proofread. List discrepancies in the TYPED "
    "version. Return STRICT JSON."
)


def build_check_user_text(typed_version, ref_version, asset_type,
                          endpoint_id=None):
    """The user-turn instruction accompanying the two images for proofreading,
    with the output-format contract re-appended for emphasis."""
    text = render_prompt(
        'CHECK_USER',
        endpoint_id=endpoint_id,
        typed_version=typed_version,
        ref_version=ref_version,
        asset_type=asset_type,
    )
    return append_format('CHECK_USER', text, endpoint_id=endpoint_id)


# Shared LaTeX delimiter contract for any prompt that emits Markdown/math.
_MATH_DELIMITER_RULES = (
    "MATHEMATICS (LaTeX delimiters — mandatory)\n"
    "- Inline math: $...$ only (e.g. $AD=x$ cm, $\\angle BAD=90^\\circ$).\n"
    "- Display math (a formula on its own line): $$...$$ only, usually on "
    "separate lines.\n"
    "- NEVER use square brackets [ ] as math delimiters — [ x+1 ] and "
    "[ \\text{Area}=... ] will NOT render.\n"
    "- NEVER use bare parentheses for labels or formulas — write $AD$, $BC$, "
    "$AB=12$, NOT (AD), (BC), or (AB=12).\n"
    "- NEVER use \\(...\\) or \\[...\\] delimiters.\n"
    "- Put NO space just inside $ delimiters: $x+1$ not $ x+1 $ (spaced "
    "delimiters do not render).\n"
    "- A LITERAL dollar sign (currency) MUST be escaped as \\$ (e.g. \\$5). "
    "Reserve unescaped $...$ / $$...$$ strictly for real mathematics.\n"
    "- Example display math:\n"
    "$$\\text{Area of trapezium }ABCD=\\frac{1}{2}(x+6)(12)$$"
)


# ==================== Markdown generation ====================

_DEFAULT_MD_SYSTEM = (
    "You are an expert at transcribing exam questions from images into clean, "
    "self-contained GitHub-Flavored Markdown (GFM).\n"
    "\n"
    "GENERAL\n"
    "- Transcribe the content faithfully and completely. Do NOT solve, answer, "
    "or add commentary.\n"
    "- Keep the original language (English and/or Chinese) exactly.\n"
    "- Output ONLY the Markdown for the question content: no code fences around "
    "the whole answer, no preamble, no explanation.\n"
    "- Use Markdown only where it helps: **bold**, *italic*, tables, math."
)


# Output-format rules for MD transcription (math delimiters, question-number
# escaping, MC option layout, the [FIGURE: ...] sentinel). The sentinel is
# consumed by ai_tools._embed_figures / FIGURE_RE — keep it intact.
_DEFAULT_MD_FORMAT = (
    _MATH_DELIMITER_RULES + "\n"
    "\n"
    "QUESTION & PART NUMBERS\n"
    "- Write the number as plain text and ESCAPE the period so Markdown does "
    "NOT turn the line into an ordered list (which adds bad indentation in the "
    "preview and Word): write 8\\. not 8. ; 8a\\. not 8a. ; 12(i)\\. not "
    "12(i). Never start a line with digits + unescaped dot + space, and never "
    "use real Markdown ordered lists (1. 2. 3.) for question numbers. Ordinary "
    "bullet lists (- item) are fine only for genuine lists in the source.\n"
    "\n"
    "MULTIPLE-CHOICE OPTIONS (A/B/C/D)\n"
    "- Put EACH option on its OWN line AND leave a BLANK LINE between "
    "consecutive options (and between the question stem and the first option). "
    "GFM needs that blank line — without it every option collapses onto a "
    "single line.\n"
    "- Start each option at column 0 as \"A.\" (label + period). Do NOT indent "
    "options with leading spaces (that nests them under a list item), and NEVER "
    "put two or more options on the same line.\n"
    "- Example (note the blank line between every option):\n"
    "8\\. Which factor contributed most to …?\n"
    "\n"
    "A. immigration from other countries\n"
    "\n"
    "B. birth rate\n"
    "\n"
    "C. death rate\n"
    "\n"
    "D. emigration to other countries\n"
    "\n"
    "FIGURES\n"
    "- ONLY for an actual diagram, figure, graph, chart, or geometric drawing "
    "that genuinely cannot be written as text or LaTeX, insert a placeholder on "
    "its own line: [FIGURE: short description]. Plain text, equations, tables, "
    "and multiple-choice options are NOT figures — never use the placeholder "
    "for them. If the question has no such drawing, emit no [FIGURE] "
    "placeholder at all."
)


_DEFAULT_MD_USER = (
    "Transcribe this {{asset_type}} image (version {{source_version}}) into "
    "Markdown following the rules. Output Markdown only. Escape dots after "
    "question numbers (8\\. not 8.). Escape literal/currency dollar signs as "
    "\\$ (write \\$5, not $5) so they are not read as LaTeX math. Put each MC "
    "option A/B/C/D on its own line with a BLANK LINE between them (otherwise "
    "they collapse onto one line). Remember: only use a [FIGURE: ...] "
    "placeholder if there is a real diagram/graph/drawing."
)


def build_md_user_text(source_version, asset_type, endpoint_id=None):
    text = render_prompt(
        'MD_USER',
        endpoint_id=endpoint_id,
        source_version=source_version,
        asset_type=asset_type,
    )
    return append_format('MD_USER', text, endpoint_id=endpoint_id)


# ==================== Solve-based ANS / SOL generation + checking ============

_DEFAULT_SOLVE_GEN_SYSTEM = (
    "You are an expert bilingual (English/Chinese) exam solver and answer-key "
    "writer. You are given the QUESTION content, and may also be given an "
    "official ENO/CHO SOLUTION to use as supporting context. Work out the "
    "problem carefully and generate ONLY the requested output."
)


# Output-format rules for solve-based generation (ANS / SOL / ANS_TEXT
# shapes, target language, math delimiters, no-preamble).
_DEFAULT_SOLVE_GEN_FORMAT = (
    "Output modes:\n"
    "- ANS: produce a concise Markdown answer. Work out the problem carefully "
    "before writing, but output only the final answer — never hedge, never "
    "contradict yourself, and never say you were wrong after reconsidering. "
    "Include only the minimum justification needed to make it unambiguous. "
    "For MC questions, the first line MUST be the single correct option "
    "letter (A/B/C/D) followed by the answer text when possible.\n"
    "- SOL: produce a complete worked Markdown solution, with clear reasoning "
    "and final answer.\n"
    "- ANS_TEXT: produce plaintext only, suitable for the database Answer Text "
    "field. For MC questions output ONLY the correct option letter A/B/C/D "
    "(optionally followed by a very short answer on the same line). Never "
    "include working, hedging, or self-correction. Do not use Markdown "
    "headings, bullets, or code fences for ANS_TEXT.\n"
    "\n"
    "Language and formatting:\n"
    "- Match the requested target version/language when possible. EN should be "
    "English, CH should be Chinese, BI should be bilingual. ENO/CHO targets "
    "should follow the visible official language.\n"
    "- For Markdown outputs:\n"
    + _MATH_DELIMITER_RULES + "\n"
    "- Output only the requested content. No preamble, no self-commentary, no "
    "markdown code fence around the whole response."
)


_DEFAULT_SOLVE_GEN_USER = (
    "Generate {{kind}} for target version {{target_version}}. The attached "
    "content contains the QUESTION first. If official ENO/CHO solution content "
    "is supplied, use it only as assistance; still produce the requested "
    "{{kind}} yourself. Output only the requested {{kind}} content."
)


_DEFAULT_SOLVE_CHECK_SYSTEM = (
    "You are a meticulous bilingual (English/Chinese) exam answer checker. "
    "You are given the QUESTION content and an existing answer/solution to "
    "check. You may also be given an official ENO/CHO SOLUTION as supporting "
    "context. Independently work out the correct answer/solution first, then "
    "judge whether the existing target is mathematically and conceptually "
    "correct, complete enough for its type, and not misleading.\n"
    "\n"
    "Check expectations:\n"
    "- ANS should contain the correct final answer, and for MC should identify "
    "the correct option.\n"
    "- SOL should contain a correct worked solution. It may use different valid "
    "methods, but must not have wrong steps, missing critical reasoning, or a "
    "wrong final answer.\n"
    "- ANS_TEXT should be a concise plaintext final answer, often just A/B/C/D "
    "for MC questions.\n"
    "- Ignore harmless wording/style/layout differences. Report only issues "
    "that affect correctness, completeness, or clarity."
)


# Output-format contract for solve-checking. Reuses parse_check_result's
# strict JSON shape — keep in sync with the parser.
_DEFAULT_SOLVE_CHECK_FORMAT = (
    "Respond with STRICT JSON only (no prose, no markdown fences) of the form:\n"
    '{"status": "ok"} when the target is correct, OR\n'
    '{"status": "issues", "issues": [{"location": "<where>", '
    '"description": "<what is wrong>", "severity": "minor|major"}]}\n'
    "Use \"major\" when the issue changes the answer or invalidates the "
    "solution."
)


_DEFAULT_SOLVE_CHECK_USER = (
    "Check {{kind}} for target version {{target_version}}. The attached "
    "content contains the QUESTION first, then the EXISTING TARGET to check. "
    "If official ENO/CHO solution content is supplied, use it as supporting "
    "context. Return STRICT JSON only."
)


def build_solve_gen_user_text(kind, target_version, endpoint_id=None):
    text = render_prompt(
        'SOLVE_GEN_USER',
        endpoint_id=endpoint_id,
        kind=kind,
        target_version=target_version,
        asset_type=kind,
    )
    return append_format('SOLVE_GEN_USER', text, endpoint_id=endpoint_id)


def build_solve_check_user_text(kind, target_version, endpoint_id=None):
    text = render_prompt(
        'SOLVE_CHECK_USER',
        endpoint_id=endpoint_id,
        kind=kind,
        target_version=target_version,
        asset_type=kind,
    )
    return append_format('SOLVE_CHECK_USER', text, endpoint_id=endpoint_id)


# ==================== Auto question tagging ====================

_DEFAULT_TAG_SYSTEM = (
    "You are a meticulous exam-question classifier. You are given image(s) of "
    "ONE exam question (and possibly its official solution), plus the list of "
    "ALLOWED tag values for its subject. Classify the question by choosing "
    "ONLY from the allowed values provided — never invent a topic / subtopic / "
    "chapter / subchapter name that is not in the list, and copy the chosen "
    "value's spelling EXACTLY.\n\n"
    "Tagging rules:\n"
    "- Every question has exactly ONE major topic and ONE major subtopic. The "
    "major subtopic MUST be one of the subtopics listed under the chosen major "
    "topic.\n"
    "- Only if the question genuinely spans more than one topic, also list the "
    "minor topic(s) it also touches and the relevant minor subtopic(s). Topics "
    "and subtopics share the same lists as the major ones. Leave these empty "
    "for a single-topic question.\n"
    "- Choose chapter / subchapter the same way (the subchapter must belong to "
    "the chosen chapter).\n"
    "- q_type: \"MC\" for a multiple-choice question, \"CQ\" for a conventional "
    "(long / structured) question.\n"
    "- level: an integer difficulty from 1 (easy) to 3 (hard).\n"
    "- section: the printed paper-section label if visible (e.g. \"A\", \"B\"), "
    "else null.\n"
    "- Tag ONLY the fields you are asked for. Use null (or [] for the list "
    "fields) for anything you cannot determine from the allowed values."
)


# Output-format contract for auto tagging. Parsed by parse_tag_result —
# keep the JSON shape in sync with the parser.
_DEFAULT_TAG_FORMAT = (
    "Respond with STRICT JSON only (no prose, no markdown fences) of the form:\n"
    '{"q_type": "MC"|"CQ"|null, "level": 1|2|3|null, "section": "..."|null, '
    '"major_topic": "..."|null, "major_subtopic": "..."|null, '
    '"minor_topics": ["..."], "subtopics": ["..."], '
    '"chapter": "..."|null, "subchapter": "..."|null}\n'
    "Use the EXACT allowed names. Include only the keys you were asked to tag."
)


_DEFAULT_TAG_USER = (
    "Subject: {{subject_name}}.\n"
    "Tag ONLY these fields: {{fields}}.\n\n"
    "Allowed values for this subject:\n{{taxonomy}}\n\n"
    "Classify the attached question image(s) (and solution if provided). "
    "Return STRICT JSON using the EXACT allowed names. Use null / [] when "
    "unsure."
)


# Human-readable labels for the field keys used across the Auto Tag UI / API.
TAG_FIELD_LABELS = OrderedDict([
    ('q_type', 'Question Type'),
    ('level', 'Level'),
    ('section', 'Section'),
    ('major_topic', 'Major Topic'),
    ('major_subtopic', 'Major Subtopic'),
    ('minor_topics', 'Minor Topics'),
    ('subtopics', 'Minor Subtopics'),
    ('chapter', 'Chapter'),
    ('subchapter', 'Subchapter'),
])

TAG_FIELDS = list(TAG_FIELD_LABELS.keys())

# Fields that draw on the subject's Topic / Subtopic taxonomy.
_TAG_TOPIC_FIELDS = {'major_topic', 'major_subtopic', 'minor_topics', 'subtopics'}
# Fields that draw on the subject's Chapter / Subchapter taxonomy.
_TAG_CHAPTER_FIELDS = {'chapter', 'subchapter'}


def build_tag_taxonomy(subject_id, fields):
    """Render the subject's allowed tag values (topics→subtopics,
    chapters→subchapters, q_type / level enums) as a compact text block for
    the Auto Tag prompt. Only the sections relevant to ``fields`` are
    included.

    ``fields`` is an iterable of field keys (see ``TAG_FIELDS``).
    """
    from app.models import Topic, Chapter

    fields = set(fields or [])
    blocks = []

    if fields & _TAG_TOPIC_FIELDS:
        topics = (Topic.query.filter_by(subject_id=subject_id)
                  .order_by(Topic.sort_order).all())
        lines = ['TOPICS (each topic, then its subtopics indented):']
        if not topics:
            lines.append('  (none defined)')
        for t in topics:
            lines.append(f'- {t.name}')
            subs = t.subtopics.all() if hasattr(t.subtopics, 'all') else list(t.subtopics)
            for s in subs:
                lines.append(f'    * {s.name}')
        blocks.append('\n'.join(lines))

    if fields & _TAG_CHAPTER_FIELDS:
        chapters = (Chapter.query.filter_by(subject_id=subject_id)
                    .order_by(Chapter.sort_order).all())
        lines = ['CHAPTERS (each chapter, then its subchapters indented):']
        if not chapters:
            lines.append('  (none defined)')
        for c in chapters:
            lines.append(f'- {c.name}')
            subs = c.subchapters.all() if hasattr(c.subchapters, 'all') else list(c.subchapters)
            for sc in subs:
                lines.append(f'    * {sc.name}')
        blocks.append('\n'.join(lines))

    if 'q_type' in fields:
        blocks.append('QUESTION TYPES: MC (multiple choice), CQ (conventional question)')
    if 'level' in fields:
        blocks.append('LEVELS: 1, 2, 3')

    return '\n\n'.join(blocks) if blocks else '(no taxonomy required)'


def build_tag_user_text(subject_name, fields, taxonomy, endpoint_id=None):
    """User-turn instruction for Auto Tag. ``fields`` is an iterable of field
    keys; rendered with human labels. The output-format contract is
    re-appended for emphasis."""
    labels = ', '.join(TAG_FIELD_LABELS.get(f, f) for f in fields) or '(none)'
    text = render_prompt('TAG_USER', endpoint_id=endpoint_id,
                         subject_name=subject_name or '(unknown)',
                         fields=labels, taxonomy=taxonomy)
    return append_format('TAG_USER', text, endpoint_id=endpoint_id)


# ==================== Robust JSON extraction helpers ====================
#
# Reasoning VLMs (Qwen-VL "thinking", etc.) often wrap their chain-of-thought
# in <think>...</think> (or similar) tags BEFORE the JSON answer, and those
# blocks routinely contain bracketed coordinate examples. A naive "first '['
# to last ']'" extraction then spans the reasoning junk and json.loads fails,
# making detection look like a random/timing failure: the raw reply clearly
# shows regions, yet parsing returns none, and re-running (a differently
# formatted reply) sometimes works. These helpers strip the reasoning and pull
# *balanced* JSON spans so the real payload is recovered deterministically.
_REASONING_TAGS = r'think|thinking|reason|reasoning|analysis|scratchpad'


def _strip_reasoning(text: str) -> str:
    """Remove reasoning/think blocks some models emit before the JSON answer."""
    if not text:
        return text or ''
    # Fully-paired blocks: <think> ... </think>.
    text = re.sub(r'<(' + _REASONING_TAGS + r')\b[^>]*>.*?</\1>', ' ',
                  text, flags=re.DOTALL | re.IGNORECASE)
    # Stray closing tag with no surviving opener: the answer is whatever
    # follows the LAST such tag.
    closes = list(re.finditer(r'</(?:' + _REASONING_TAGS + r')>', text,
                              re.IGNORECASE))
    if closes:
        text = text[closes[-1].end():]
    return text


def _balanced_spans(text: str, open_ch: str, close_ch: str):
    """Return every top-level balanced ``open_ch..close_ch`` substring, string-
    and escape-aware so brackets inside JSON string values don't miscount."""
    spans = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            if depth == 0:
                start = i
            depth += 1
        elif ch == close_ch and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                spans.append(text[start:i + 1])
                start = -1
    return spans


def _json_candidates(text: str, prefer: str = '['):
    """Ordered, de-duplicated list of candidate strings to try with
    ``json.loads``. Robust to reasoning blocks, code fences, and surrounding
    prose. ``prefer`` is the opening bracket of the expected top-level
    container (``'['`` for a list, ``'{'`` for an object). Balanced spans are
    tried longest-first so the real payload wins over stray bracketed examples
    left in prose.

    Only ``prefer``-type spans are emitted: a wrapped list ``{"boxes": [...]}``
    is still recovered because the inner ``[...]`` is itself a balanced span,
    while mixing single ``{...}`` object spans into a list parser would let one
    object short-circuit the loop with an empty result.
    """
    text = _strip_reasoning(text or '')
    cands = []

    def add(s):
        s = (s or '').strip()
        if s and s not in cands:
            cands.append(s)

    add(text)
    for m in re.finditer(r'```(?:json)?\s*(.*?)```', text, re.DOTALL):
        add(m.group(1))
    close = ']' if prefer == '[' else '}'
    for s in sorted(_balanced_spans(text, prefer, close), key=len, reverse=True):
        add(s)
    return cands


def _as_item_list(data, wrapper_keys):
    """Coerce a parsed JSON value into a list of item dicts: a bare list, a
    ``{wrapper_key: [...]}`` object, or a single item dict (one carrying a
    ``box``). Returns ``[]`` for a dict that matches none of these (so the
    caller keeps trying other candidates rather than short-circuiting) and
    ``None`` for a non-list/non-dict value."""
    if isinstance(data, dict):
        for k in wrapper_keys:
            v = data.get(k)
            if isinstance(v, list):
                return v
        if any(k in data for k in ('box', 'bbox', 'bounding_box')):
            return [data]
        return []
    if isinstance(data, list):
        return data
    return None


# ---- Tolerant salvage for malformed model JSON ----
#
# Vision models occasionally emit *almost*-JSON that ``json.loads`` rejects
# wholesale — e.g. a stray duplicate key: ``"box": [...], "label": "continues_prev":
# false``. That single corrupt object used to take its valid neighbours down
# with it (the whole array fails → empty → "no regions"), which looks random
# because the model only corrupts the reply some of the time. As a last resort
# we recover each brace-balanced object that contains a 4-number box and read
# its other fields with targeted regexes, so one broken object can't drop the
# rest.
_SALVAGE_NUM_RE = re.compile(r'-?\d+(?:\.\d+)?')
_SALVAGE_BOX_RE = re.compile(
    r'"(?:box|bbox|bounding_box)"\s*:\s*\[([^\[\]]*)\]', re.DOTALL)


def _salvage_box_objects(text: str):
    """Return ``[(raw_object_str, [x1,y1,x2,y2]), ...]`` for every brace-
    balanced object carrying a 4-number box, even when the surrounding JSON is
    invalid. Coordinates are raw (un-normalised); the caller normalises."""
    out = []
    for obj in _balanced_spans(_strip_reasoning(text or ''), '{', '}'):
        m = _SALVAGE_BOX_RE.search(obj)
        if not m:
            continue
        nums = _SALVAGE_NUM_RE.findall(m.group(1))
        if len(nums) != 4:
            continue
        try:
            box = [float(n) for n in nums]
        except (ValueError, TypeError):
            continue
        out.append((obj, box))
    return out


def _salvage_int(raw: str, *keys):
    for k in keys:
        m = re.search(r'"' + k + r'"\s*:\s*"?\s*(-?\d+)', raw)
        if m:
            return int(m.group(1))
    return None


def _salvage_bool(raw: str, key: str) -> bool:
    m = re.search(r'"' + key + r'"\s*:\s*(true|false)', raw, re.IGNORECASE)
    return bool(m and m.group(1).lower() == 'true')


def _salvage_str(raw: str, *keys):
    for k in keys:
        m = re.search(r'"' + k + r'"\s*:\s*"([^"]*)"', raw)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


def parse_tag_result(text: str):
    """Parse the Auto Tag model output into a normalised dict:
    ``{q_type, level, section, major_topic, major_subtopic, minor_topics[],
    subtopics[], chapter, subchapter}``.

    Tolerant of code fences and surrounding prose (mirrors
    ``parse_check_result``). Returns ``None`` on total failure.
    """
    if not text:
        return None
    candidates = _json_candidates(text, '{')

    def _clean_str(v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    def _clean_list(v):
        if not isinstance(v, (list, tuple)):
            if v in (None, ''):
                return []
            v = [v]
        out = []
        for it in v:
            s = _clean_str(it)
            if s:
                out.append(s)
        return out

    for c in candidates:
        try:
            data = json.loads(c)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue

        # q_type
        qt = _clean_str(data.get('q_type'))
        if qt:
            qt = qt.upper()
            qt = qt if qt in ('MC', 'CQ') else None
        # level
        level = None
        lvl_raw = data.get('level')
        if lvl_raw not in (None, ''):
            m = re.search(r'[123]', str(lvl_raw))
            if m:
                level = int(m.group(0))

        return {
            'q_type': qt,
            'level': level,
            'section': _clean_str(data.get('section')),
            'major_topic': _clean_str(data.get('major_topic')),
            'major_subtopic': _clean_str(data.get('major_subtopic')),
            'minor_topics': _clean_list(data.get('minor_topics')),
            'subtopics': _clean_list(data.get('subtopics')),
            'chapter': _clean_str(data.get('chapter')),
            'subchapter': _clean_str(data.get('subchapter')),
        }
    return None


# ==================== Question explanation (AI tutor chat) ====================

_DEFAULT_EXPLAIN_SYSTEM = (
    "You are an expert, encouraging exam tutor helping a student understand ONE "
    "exam question. You are given image(s) of the QUESTION and, when available, "
    "its official SOLUTION. Explain it clearly and pedagogically:\n"
    "- First restate, in plain language, what the question is asking.\n"
    "- Explain the key concepts, then walk through the reasoning step by step, "
    "justifying each step rather than just stating it.\n"
    "- If a SOLUTION is provided, base your explanation on it and expand any "
    "terse steps; if not, work the problem out yourself and give the answer.\n"
    "- Keep the student's language (English and/or Chinese). Be concise but "
    "complete, and answer any follow-up questions in the same style."
)


# Output-format (math delimiter) rules for the Explain tutor — the chat UI
# renders $...$ / $$...$$ via KaTeX, so bracket-style math will not render.
_DEFAULT_EXPLAIN_FORMAT = (
    "Use LaTeX for ALL mathematics. Some models wrongly emit [ ] or bare ( ) "
    "around formulas — that will NOT render here. Follow these rules exactly:\n"
    + _MATH_DELIMITER_RULES
)

_DEFAULT_EXPLAIN_INITIAL_USER = (
    "Please explain this question: what it is asking, the concepts involved, "
    "and how to arrive at the answer step by step. Use $...$ / $$...$$ for "
    "all math — never square brackets [ ] or bare parentheses like (AD) for "
    "labels or formulas."
)


def build_explain_initial_user_text(endpoint_id=None):
    """Initial user-turn instruction for the Explain tutor, with the math
    formatting rules re-appended for emphasis."""
    text = get_prompt('EXPLAIN_INITIAL_USER', endpoint_id)
    return append_format('EXPLAIN_INITIAL_USER', text, endpoint_id=endpoint_id)


# ---- Figure placeholders + bounding-box localisation (for cropping) --------

# Matches a figure placeholder like "[FIGURE]" or "[FIGURE: a right triangle]".
# Deliberately excludes markdown links ("[text](url)") because a closing "]"
# is required with no "(" semantics here.
FIGURE_RE = re.compile(r'\[FIGURE\b\s*:?\s*([^\]]*)\]', re.IGNORECASE)


def has_figure_placeholder(md: str) -> bool:
    return bool(FIGURE_RE.search(md or ''))


def figure_captions(md: str):
    """Return the caption text of every [FIGURE: ...] placeholder, in order."""
    return [m.group(1).strip() for m in FIGURE_RE.finditer(md or '')]


# Shared 0-1000 grid contract (same convention as PDF batch import).
_DEFAULT_FIGURE_BOX_JSON_CONTRACT = (
    "Return STRICT JSON only (no prose, no markdown fences): a list, in "
    "top-to-bottom reading order, of objects of the form\n"
    '{"caption": "<short description>", "box": {{box_array}}}\n'
    "COORDINATES: integers on a 0-1000 grid measured from the TOP-LEFT corner "
    "of the image. x is the HORIZONTAL position (x=0 is the left edge, x=1000 "
    "the right edge); y is the VERTICAL position (y=0 is the TOP edge, y=1000 "
    "the BOTTOM edge). The box is {{box_corner}}. Draw a TIGHT box around each "
    "diagram only — exclude surrounding question text, equations, tables, and "
    "multiple-choice options. Example: a diagram in the upper-middle of the "
    "image might be "
    '{"caption": "right triangle", "box": {{box_example}}}. '
    "If there are no figures, return []."
)

_DEFAULT_FIGURE_BOX_SYSTEM = (
    "You are a precise vision tool that locates figures in an exam-question "
    "image. A 'figure' is a diagram, graph, chart, geometric drawing, or "
    "picture — NOT plain text, equations, tables, or multiple-choice options.\n"
    "Locate every real figure on the image.\n\n"
    "{{json_contract}}"
)

_DEFAULT_FIGURE_BOX_USER = (
    "List the bounding boxes of the real figures/diagrams in this image as "
    "STRICT JSON. Use integer coordinates on a 0-1000 grid in the order "
    "{{box_pairs}}, measured from the top-left corner. Return [] if none."
)


def parse_figure_boxes(text: str, img_w=None, img_h=None, coord_order='xyxy'):
    """Parse the figure-box model output into a list of
    ``{caption, box:[x1,y1,x2,y2]}`` with fractional coords clamped to 0..1.

    Uses the same normalisation as :func:`parse_question_boxes` (0-1000 grid,
    0..1 fractions, raw pixels vs downscaled ``img_w``/``img_h``, and
    ``coord_order`` for Gemini-style y-first tuples). Returns ``[]`` on any
    failure (caller then falls back to embedding the whole image).
    """
    if not text:
        return []

    def _emit(items):
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            box = it.get('box') or it.get('bbox') or it.get('bounding_box')
            if not (isinstance(box, (list, tuple)) and len(box) == 4):
                continue
            try:
                coords = [float(v) for v in box]
            except (ValueError, TypeError):
                continue
            x1, y1, x2, y2 = _normalize_box(coords, img_w, img_h, coord_order)
            out.append({'caption': str(it.get('caption', '') or ''),
                        'box': [x1, y1, x2, y2]})
        return out

    for c in _json_candidates(text, '['):
        try:
            data = json.loads(c)
        except (ValueError, TypeError):
            continue
        items = _as_item_list(data, ('figures', 'boxes'))
        if items is None:
            continue
        parsed = _emit(items)
        if parsed:
            return parsed

    out = []
    for raw, box in _salvage_box_objects(text):
        x1, y1, x2, y2 = _normalize_box(box, img_w, img_h, coord_order)
        out.append({'caption': _salvage_str(raw, 'caption') or '',
                    'box': [x1, y1, x2, y2]})
    return out


# ==================== PDF batch import (question region detection) ====================

# Shared tail describing the STRICT JSON contract for both QUE and SOL prompts.
#
# Coordinate convention: an explicit 0-1000 integer grid with the ORIGIN at the
# TOP-LEFT, plus a worked numeric example. The AXIS ORDER is NOT hardcoded —
# the {{box_array}} / {{box_corner}} / {{box_example}} placeholders are filled
# from the PDF_IMPORT_COORD_ORDER setting (xyxy vs yxyx) by pdf_box_order_vars,
# so the instruction the model sees matches what parse_question_boxes expects.
# Vision models disagree wildly on box conventions (0..1 vs 0..1000 vs raw
# pixels; x-first vs y-first); we pin one convention and parse defensively.
_DEFAULT_PDF_BOX_JSON_CONTRACT = (
    "Return STRICT JSON only (no prose, no markdown fences): a list, in "
    "top-to-bottom reading order, of objects of the form\n"
    '{"qno": <integer printed question number>, "box": {{box_array}}, '
    '"continues_prev": <true|false>, "continues_next": <true|false>}\n'
    "COORDINATES: integers on a 0-1000 grid measured from the TOP-LEFT corner "
    "of the page. x is the HORIZONTAL position (x=0 is the left edge, x=1000 "
    "the right edge); y is the VERTICAL position (y=0 is the TOP edge, y=1000 "
    "the BOTTOM edge). The box is {{box_corner}}. Example: a question that "
    "fills the TOP THIRD of the page across almost the full width is "
    '{"qno": 1, "box": {{box_example}}, "continues_prev": false, '
    '"continues_next": false} — note the small vertical values because it is '
    "near the TOP. \"qno\" is the PRINTED question number you can read on the "
    "page (an integer; for a part like \"5\" use 5). Set \"continues_prev\" to "
    "true when the topmost region is the tail of a question that began on the "
    "previous page, and \"continues_next\" to true when the bottom region is "
    "cut off and continues on the next page. If the page has no question "
    "content, return []."
)

_DEFAULT_PDF_QUE_BOX_SYSTEM = (
    "You are a precise document-layout tool for Hong Kong DSE exam papers. "
    "You are given ONE rasterised page of a QUESTION paper. Locate the tight "
    "bounding box around each individual exam QUESTION on the page.\n"
    "- The box must enclose the full question text together with any figures, "
    "diagrams, graphs, tables, and multiple-choice options that belong to it.\n"
    "- INCLUDE the marks allocation printed with the question — e.g. "
    "\"(4 marks)\", \"(2 marks)\", \"(7 分)\" — even when it sits a little BELOW "
    "the last line of text or to the lower-right, separated by a small gap. It "
    "is part of the question, so extend the BOTTOM (and right) of the box to "
    "cover it; do NOT stop the box at the last line of text when a marks "
    "annotation follows below it.\n"
    "- EXCLUDE the blank answering/working space (white space or ruled lines "
    "left for the candidate's answer), running headers/footers, page numbers, "
    "and the outer page margins. Crop tightly to the printed question content "
    "only — but note the marks annotation above is NOT answer space, so keep "
    "it.\n"
    "- Each numbered question is one box. Do NOT split a question into its "
    "sub-parts (a), (b), (c) — keep the whole numbered question together.\n"
    "- The page is a single column; questions are stacked vertically.\n\n"
    "{{json_contract}}"
)

_DEFAULT_PDF_SOL_BOX_SYSTEM = (
    "You are a precise document-layout tool for Hong Kong DSE exam papers. "
    "You are given ONE rasterised page of a SOLUTION / marking-scheme "
    "document. Locate the tight bounding box around each individual "
    "SOLUTION on the page.\n"
    "- The box must enclose the COMPLETE solution: every worked step, plus any "
    "supplementary notes, marking annotations, comments, or remarks printed to "
    "the RIGHT-HAND SIDE of the working. Do not cut those off.\n"
    "- Only EXCLUDE running headers/footers, page numbers, and the outer page "
    "margins.\n"
    "- Each numbered question's solution is one box. Keep all sub-parts of one "
    "numbered question together.\n"
    "- The page is a single column of solutions stacked vertically (side notes "
    "do not count as a second column).\n\n"
    "{{json_contract}}"
)


_DEFAULT_PDF_BOX_USER = (
    "List the bounding boxes of every {{what}} on this page as STRICT JSON. "
    "Use integer coordinates on a 0-1000 grid in the order {{box_pairs}}, "
    "measured from the top-left corner. Include the printed question number "
    "for each. Return [] if the page has no {{what}}."
)


# ---- Generic extraction mode ----------------------------------------------
#
# A general-purpose region detector with NO exam-question context: the caller
# supplies a free-text description of WHAT to extract (e.g. "every table",
# "each chart with its caption"). The model returns a short label + box per
# matching region. Coordinate handling mirrors the question-box contract so
# the same {{box_array}}/{{box_corner}}/{{box_example}}/{{box_pairs}} order
# variables apply and parse_generic_boxes can reuse _normalize_box.
_DEFAULT_PDF_GENERIC_BOX_JSON_CONTRACT = (
    "Return STRICT JSON only (no prose, no markdown fences): a list, in "
    "top-to-bottom reading order, of objects of the form\n"
    '{"label": <short descriptive name>, "box": {{box_array}}}\n'
    "COORDINATES: integers on a 0-1000 grid measured from the TOP-LEFT corner "
    "of the page. x is the HORIZONTAL position (x=0 is the left edge, x=1000 "
    "the right edge); y is the VERTICAL position (y=0 is the TOP edge, y=1000 "
    "the BOTTOM edge). The box is {{box_corner}}. Example: a region that fills "
    "the TOP THIRD of the page across almost the full width is "
    '{"label": "Table 1", "box": {{box_example}}} — note the small vertical '
    "values because it is near the TOP. \"label\" is a short human-readable "
    "name for the region (e.g. \"Table 1\", \"Figure 2\", \"Chart\"); keep it "
    "filename-safe. If the page has nothing matching the request, return []."
)

_DEFAULT_PDF_GENERIC_BOX_SYSTEM = (
    "You are a precise, general-purpose document-layout tool. You are given "
    "ONE rasterised page of a document. The user tells you WHAT to extract; "
    "locate a tight bounding box around each region on the page that matches "
    "their request.\n"
    "- Return one box per distinct matching region.\n"
    "- Each box must enclose the WHOLE region, including its title/caption/"
    "labels, and exclude unrelated surrounding content and the outer page "
    "margins.\n"
    "- The page may use any layout (single or multi column). Read top to "
    "bottom, then left to right.\n"
    "- If nothing on the page matches, return an empty list.\n\n"
    "THE USER'S EXTRACTION REQUEST:\n{{instruction}}\n\n"
    "{{json_contract}}"
)

_DEFAULT_PDF_GENERIC_BOX_USER = (
    "Extract every region matching this request from the page as STRICT JSON: "
    "{{instruction}}\n"
    "Use integer coordinates on a 0-1000 grid in the order {{box_pairs}}, "
    "measured from the top-left corner, and give each region a short "
    "filename-safe label. Return [] if nothing on this page matches."
)


# ---- Anchor mode (LLM assisted: segment) -----------------------------------
#
# The "segment" detection method asks the model for ONLY the vertical START
# position of each question — a single scalar per question, which a vision LLM
# localises far more reliably than a tight 4-tuple box. Classical CV
# (app/pdf_layout.segment_page) then derives each question's true extent from
# the whitespace gaps between consecutive anchors.
_DEFAULT_PDF_ANCHOR_JSON_CONTRACT = (
    "Return STRICT JSON only (no prose, no markdown fences): a list, in "
    "top-to-bottom order, of objects of the form\n"
    '{"qno": <integer question number>, "y": <start position 0-1000>}\n'
    "y is the VERTICAL position of the START (top) of the item — the line "
    "where its printed number appears — measured from the TOP of the page on a "
    "0-1000 grid (y=0 is the very top, y=1000 the very bottom). You do NOT need "
    "to say where each item ends, how tall it is, or its left/right extent — "
    "ONLY where each one begins. Example: a page whose first question starts "
    "near the top and whose second starts just below the middle -> "
    '[{"qno": 1, "y": 60}, {"qno": 2, "y": 540}]. "qno" is the PRINTED number '
    "(an integer). If the page has no relevant content, return []."
)

_DEFAULT_PDF_ANCHOR_SYSTEM = (
    "You are a precise document-layout tool for Hong Kong DSE exam papers. "
    "You are given ONE rasterised page. Identify where each individual "
    "{{what}} on the page BEGINS, top to bottom.\n"
    "- Report one entry per numbered {{what}}, using its printed number.\n"
    "- Give ONLY the vertical START position of each (the line with its "
    "number). The precise crop is computed separately, so do not draw boxes "
    "or estimate heights.\n"
    "- A {{what}} that is the continuation (tail) of one begun on the previous "
    "page has no printed number at the top — only list items whose number you "
    "can actually read.\n"
    "- The page is a single column, stacked vertically.\n\n"
    "{{json_contract}}"
)

_DEFAULT_PDF_ANCHOR_USER = (
    "List where every {{what}} on this page BEGINS as STRICT JSON: its printed "
    "number and its vertical start position on a 0-1000 grid (0=top, "
    "1000=bottom). Return [] if the page has no {{what}}."
)


_DEFAULT_PDF_PAPER_NAME_SYSTEM = (
    "You identify the paper code of a Hong Kong public-exam paper (HKDSE, "
    "HKCEE, HKALE) from its first page and file name. The paper code has the "
    "form SUBJECT_SOURCE_YEAR_PAPER where:\n"
    "- SUBJECT is the question bank's short subject code (uppercase letters/"
    "digits). Choose the BEST match from the allowed list when one is given.\n"
    "- SOURCE is exactly one of DSE (HKDSE), CE (HKCEE), or AL (HKALE).\n"
    "- YEAR is the 4-digit exam year.\n"
    "- PAPER starts with 'P' followed by the paper number/letter, e.g. P1, "
    "P2, P1A, PIB. If the page shows 'Paper 1' use P1, 'Paper 2B' use P2B.\n\n"
    "Use BOTH the file name and the visible text on the page (subject title, "
    "year, 'PAPER 1', exam authority logos/headers). Prefer the page content "
    "when it conflicts with the file name.\n\n"
    "Respond with STRICT JSON only (no prose, no markdown fences):\n"
    '{"paper": "SUBJECT_SOURCE_YEAR_PAPER", "confidence": <0-1>} when you can '
    "determine it, OR\n"
    '{"paper": null, "confidence": 0} when you genuinely cannot tell.\n'
    "Do NOT include the question number — only the paper-level code."
)


_DEFAULT_PDF_PAPER_NAME_USER = (
    "File name: {{filename}}\n"
    "Allowed subject codes: {{subjects}}\n\n"
    "Identify this paper's code as SUBJECT_SOURCE_YEAR_PAPER. Return STRICT "
    "JSON only."
)


# ---- Registry --------------------------------------------------------------
#
# Order here drives the order rendered in the admin UI; group keys also
# define the section ordering. Variables declared here are the ONLY names
# that ``render_prompt`` will recognise; passing extra kwargs is fine
# (they'll just be ignored by the substitution regex).

PROMPTS_REGISTRY = OrderedDict([
    ('CHECK_SYSTEM', _prompt(
        group='AI Tools — Proofreading',
        label='Proofreading: System prompt',
        description=(
            'Role and contract for the proofreader. The model receives the '
            'OFFICIAL scan first, then the TYPED reproduction, and must reply '
            'with STRICT JSON of the documented shape. Changing this prompt '
            'risks breaking the JSON parser — keep the response contract.'
        ),
        default=_DEFAULT_CHECK_SYSTEM,
        role='system',
        format_key='CHECK_FORMAT',
    )),
    ('CHECK_USER', _prompt(
        group='AI Tools — Proofreading',
        label='Proofreading: User-turn instruction',
        description=(
            'Accompanies the two images sent each call. Variables are filled '
            'in per-question by the AI Tools batch op. The CHECK_FORMAT block '
            'is re-appended at the end of the user turn.'
        ),
        default=_DEFAULT_CHECK_USER,
        variables=['asset_type', 'ref_version', 'typed_version'],
        role='user',
        format_key='CHECK_FORMAT',
    )),
    ('CHECK_FORMAT', _prompt(
        group='AI Tools — Proofreading',
        label='Proofreading: Output format rules',
        description=(
            'STRICT JSON response contract for proofreading. Injected into '
            'the system prompt AND appended to the user turn (some models '
            'under-weight system-role formatting rules). Changing the JSON '
            'shape risks breaking parse_check_result — keep the contract.'
        ),
        default=_DEFAULT_CHECK_FORMAT,
        role='format',
    )),
    ('MD_SYSTEM', _prompt(
        group='AI Tools — Markdown Generation',
        label='Markdown generation: System prompt',
        description=(
            'Rules for transcribing question images into self-contained '
            'GitHub-Flavored Markdown. Requires escaped question numbers '
            '(8\\.) and one MC option per line. The [FIGURE: ...] placeholder '
            'rule is consumed downstream by the figure-embed pass — keep that '
            'sentinel intact if you customise this prompt.'
        ),
        default=_DEFAULT_MD_SYSTEM,
        role='system',
        format_key='MD_FORMAT',
    )),
    ('MD_USER', _prompt(
        group='AI Tools — Markdown Generation',
        label='Markdown generation: User-turn instruction',
        description=(
            'Accompanies the source image(s) sent each call. The MD_FORMAT '
            'block is re-appended at the end of the user turn.'
        ),
        default=_DEFAULT_MD_USER,
        variables=['asset_type', 'source_version'],
        role='user',
        format_key='MD_FORMAT',
    )),
    ('MD_FORMAT', _prompt(
        group='AI Tools — Markdown Generation',
        label='Markdown generation: Output format rules',
        description=(
            'Math-delimiter, question-number escaping, MC-option, and '
            '[FIGURE: ...] rules for MD transcription. Injected into the '
            'system prompt AND appended to the user turn. The [FIGURE: ...] '
            'sentinel is consumed downstream by the figure-embed pass — keep '
            'it intact if you customise this block.'
        ),
        default=_DEFAULT_MD_FORMAT,
        role='format',
    )),
    ('SOLVE_GEN_SYSTEM', _prompt(
        group='AI Tools — Solve',
        label='Solve generation: System prompt',
        description=(
            'Rules for LLM-generated ANS / SOL Markdown and ANS Text. Unlike '
            'Markdown generation, this prompt asks the model to solve the '
            'question rather than transcribe a source image.'
        ),
        default=_DEFAULT_SOLVE_GEN_SYSTEM,
        role='system',
        format_key='SOLVE_GEN_FORMAT',
    )),
    ('SOLVE_GEN_USER', _prompt(
        group='AI Tools — Solve',
        label='Solve generation: User-turn instruction',
        description=(
            'Accompanies the question content and optional official solution '
            'context. The SOLVE_GEN_FORMAT block is re-appended at the end '
            'of the user turn.'
        ),
        default=_DEFAULT_SOLVE_GEN_USER,
        variables=['kind', 'target_version', 'asset_type'],
        role='user',
        format_key='SOLVE_GEN_FORMAT',
    )),
    ('SOLVE_GEN_FORMAT', _prompt(
        group='AI Tools — Solve',
        label='Solve generation: Output format rules',
        description=(
            'Output-mode (ANS / SOL / ANS_TEXT), target-language, and math '
            'delimiter rules for solve-based generation. Injected into the '
            'system prompt AND appended to the user turn.'
        ),
        default=_DEFAULT_SOLVE_GEN_FORMAT,
        role='format',
    )),
    ('SOLVE_CHECK_SYSTEM', _prompt(
        group='AI Tools — Solve',
        label='Solve check: System prompt',
        description=(
            'Strict JSON checker for ANS / SOL / ANS Text correctness after '
            'the model independently works out the question. Keep the JSON '
            'shape compatible with parse_check_result.'
        ),
        default=_DEFAULT_SOLVE_CHECK_SYSTEM,
        role='system',
        format_key='SOLVE_CHECK_FORMAT',
    )),
    ('SOLVE_CHECK_USER', _prompt(
        group='AI Tools — Solve',
        label='Solve check: User-turn instruction',
        description=(
            'Accompanies the question content, existing target, and optional '
            'official solution context. The SOLVE_CHECK_FORMAT block is '
            're-appended at the end of the user turn.'
        ),
        default=_DEFAULT_SOLVE_CHECK_USER,
        variables=['kind', 'target_version', 'asset_type'],
        role='user',
        format_key='SOLVE_CHECK_FORMAT',
    )),
    ('SOLVE_CHECK_FORMAT', _prompt(
        group='AI Tools — Solve',
        label='Solve check: Output format rules',
        description=(
            'STRICT JSON response contract for solve-checking. Injected into '
            'the system prompt AND appended to the user turn. Keep the JSON '
            'shape compatible with parse_check_result.'
        ),
        default=_DEFAULT_SOLVE_CHECK_FORMAT,
        role='format',
    )),
    ('TAG_SYSTEM', _prompt(
        group='AI Tools — Auto Tagging',
        label='Auto tagging: System prompt',
        description=(
            'Role + STRICT JSON contract for the Auto Question Tagging '
            'feature. The model is shown the question (and optional solution) '
            'image(s) plus the subject\'s allowed tag values, and must reply '
            'with JSON using the EXACT allowed names. Changing the JSON shape '
            'risks breaking parse_tag_result — keep the response contract.'
        ),
        default=_DEFAULT_TAG_SYSTEM,
        role='system',
        format_key='TAG_FORMAT',
    )),
    ('TAG_USER', _prompt(
        group='AI Tools — Auto Tagging',
        label='Auto tagging: User-turn instruction',
        description=(
            'Accompanies the question image(s) each call. Variables are filled '
            'in per-question: the subject name, the list of fields to tag, and '
            'the rendered allowed-values taxonomy for the subject. The '
            'TAG_FORMAT block is re-appended at the end of the user turn.'
        ),
        default=_DEFAULT_TAG_USER,
        variables=['subject_name', 'fields', 'taxonomy'],
        role='user',
        format_key='TAG_FORMAT',
    )),
    ('TAG_FORMAT', _prompt(
        group='AI Tools — Auto Tagging',
        label='Auto tagging: Output format rules',
        description=(
            'STRICT JSON response contract for auto tagging. Injected into '
            'the system prompt AND appended to the user turn. Changing the '
            'JSON shape risks breaking parse_tag_result — keep the contract.'
        ),
        default=_DEFAULT_TAG_FORMAT,
        role='format',
    )),
    ('EXPLAIN_SYSTEM', _prompt(
        group='Explain Tutor (Dashboard)',
        label='Explain: System prompt',
        description=(
            'Persona + rules for the dashboard Explain tutor chat. The model '
            'is shown the QUESTION image and (when available) the official '
            'SOLUTION image, and replies in Markdown + LaTeX math.'
        ),
        default=_DEFAULT_EXPLAIN_SYSTEM,
        role='system',
        format_key='EXPLAIN_FORMAT',
    )),
    ('EXPLAIN_INITIAL_USER', _prompt(
        group='Explain Tutor (Dashboard)',
        label='Explain: Initial user request',
        description=(
            'Trailing text appended after the QUESTION/SOLUTION images on the '
            'first user turn. Keep it short — it just kicks off the tutor '
            'response. Follow-up turns are user free-text and use no prompt. '
            'The EXPLAIN_FORMAT block is re-appended at the end of this turn.'
        ),
        default=_DEFAULT_EXPLAIN_INITIAL_USER,
        role='user',
        format_key='EXPLAIN_FORMAT',
    )),
    ('EXPLAIN_FORMAT', _prompt(
        group='Explain Tutor (Dashboard)',
        label='Explain: Output format rules',
        description=(
            'Math-delimiter rules for the Explain tutor chat (the UI renders '
            '$...$ / $$...$$ via KaTeX). Injected into the system prompt AND '
            'appended to the initial user turn.'
        ),
        default=_DEFAULT_EXPLAIN_FORMAT,
        role='format',
    )),
    ('FIGURE_BOX_JSON_CONTRACT', _prompt(
        group='Figure Detection (MD Generation)',
        label='Figure bbox: JSON contract (shared)',
        description=(
            'JSON-shape + 0-1000 coordinate contract appended to the figure '
            'bbox system prompt via {{json_contract}}. Uses the same grid as '
            'PDF batch import; parsed by parse_figure_boxes (honours '
            'PDF_IMPORT_COORD_ORDER for y-first models like Gemini). '
            'The {{box_array}} / {{box_corner}} / {{box_example}} placeholders '
            'are filled from the PDF_IMPORT_COORD_ORDER system setting (xyxy '
            'vs yxyx) so the coordinate order is NOT hardcoded — matching '
            'the parser exactly.'
        ),
        default=_DEFAULT_FIGURE_BOX_JSON_CONTRACT,
        variables=['box_array', 'box_corner', 'box_example'],
        role='format',
    )),
    ('FIGURE_BOX_SYSTEM', _prompt(
        group='Figure Detection (MD Generation)',
        label='Figure bbox: System prompt',
        description=(
            'Used during MD generation when a [FIGURE: ...] placeholder is '
            'present and embed_image is enabled, to locate the figure region '
            'for cropping. STRICT JSON contract; coordinates are 0-1000 '
            'integers (normalised by parse_figure_boxes). Changing this risks '
            'breaking parse_figure_boxes.'
        ),
        default=_DEFAULT_FIGURE_BOX_SYSTEM,
        variables=['json_contract'],
        role='system',
    )),
    ('FIGURE_BOX_USER', _prompt(
        group='Figure Detection (MD Generation)',
        label='Figure bbox: User-turn instruction',
        description=(
            "Accompanies the single source image to localise figures in. "
            "{{box_pairs}} is filled from the PDF_IMPORT_COORD_ORDER system "
            "setting (xyxy vs yxyx) so the axis-order instruction matches the "
            "parser."
        ),
        default=_DEFAULT_FIGURE_BOX_USER,
        variables=['box_pairs'],
        role='user',
        format_key='FIGURE_BOX_JSON_CONTRACT',
    )),
    ('PDF_BOX_JSON_CONTRACT', _prompt(
        group='PDF Batch Import — Question/Solution Detection',
        label='PDF bbox: JSON contract (shared)',
        description=(
            'Common JSON-shape contract appended to BOTH the Question and '
            'Solution detection system prompts via the {{json_contract}} '
            'placeholder. Edit ONCE here to update the response shape '
            'expected from the model on both sides; the parser '
            '(parse_question_boxes) is tightly coupled to this contract. '
            'The {{box_array}} / {{box_corner}} / {{box_example}} placeholders '
            'are filled from the PDF_IMPORT_COORD_ORDER system setting (xyxy '
            'vs yxyx) so the coordinate order is NOT hardcoded.'
        ),
        default=_DEFAULT_PDF_BOX_JSON_CONTRACT,
        variables=['box_array', 'box_corner', 'box_example'],
        role='format',
    )),
    ('PDF_QUE_BOX_SYSTEM', _prompt(
        group='PDF Batch Import — Question/Solution Detection',
        label='PDF bbox: Question-page system prompt',
        description=(
            'Used by PDF Batch Import to detect each individual question on a '
            'rasterised exam page. Includes specific guidance for marks '
            'annotations, multi-part questions, and excluding answer space. '
            'The {{json_contract}} placeholder is replaced with the shared '
            'JSON contract above.'
        ),
        default=_DEFAULT_PDF_QUE_BOX_SYSTEM,
        variables=['json_contract'],
        role='system',
    )),
    ('PDF_SOL_BOX_SYSTEM', _prompt(
        group='PDF Batch Import — Question/Solution Detection',
        label='PDF bbox: Solution-page system prompt',
        description=(
            'Used by PDF Batch Import to detect each individual solution on a '
            'rasterised marking-scheme page. Keeps right-hand marking '
            'annotations (which the QUE prompt does not). The '
            '{{json_contract}} placeholder is replaced with the shared JSON '
            'contract above.'
        ),
        default=_DEFAULT_PDF_SOL_BOX_SYSTEM,
        variables=['json_contract'],
        role='system',
    )),
    ('PDF_BOX_USER', _prompt(
        group='PDF Batch Import — Question/Solution Detection',
        label='PDF bbox: User-turn instruction',
        description=(
            "Accompanies the single page image. {{what}} is filled with "
            "either 'questions' or 'solutions' depending on which side is "
            "being processed. {{box_pairs}} is filled from the "
            "PDF_IMPORT_COORD_ORDER system setting (xyxy vs yxyx)."
        ),
        default=_DEFAULT_PDF_BOX_USER,
        variables=['what', 'box_pairs'],
        role='user',
        format_key='PDF_BOX_JSON_CONTRACT',
    )),
    ('PDF_GENERIC_BOX_JSON_CONTRACT', _prompt(
        group='PDF Batch Import — Generic Extraction',
        label='PDF generic: JSON contract (shared)',
        description=(
            'Response contract for the "Generic Extraction" task (no exam '
            'context): the model returns {label, box} per matching region. '
            'Substituted into the generic system prompt via {{json_contract}}. '
            'The {{box_array}} / {{box_corner}} / {{box_example}} placeholders '
            'are filled from the PDF_IMPORT_COORD_ORDER setting. Parser '
            'parse_generic_boxes is coupled to this shape. ALSO powers the '
            'Toolbox → PDF Tool → Find & Mark "AI detect" engine.'
        ),
        default=_DEFAULT_PDF_GENERIC_BOX_JSON_CONTRACT,
        variables=['box_array', 'box_corner', 'box_example'],
        role='format',
    )),
    ('PDF_GENERIC_BOX_SYSTEM', _prompt(
        group='PDF Batch Import — Generic Extraction',
        label='PDF generic: System prompt',
        description=(
            'Used by the "Generic Extraction" task to find regions matching a '
            'user-supplied request on any document page (no exam-question '
            'context). {{instruction}} is the user request; {{json_contract}} '
            'is replaced with the generic contract above. ALSO powers the '
            'Toolbox → PDF Tool → Find & Mark "AI detect" engine (both the '
            '"find the search terms" and "custom instruction" modes feed '
            '{{instruction}}).'
        ),
        default=_DEFAULT_PDF_GENERIC_BOX_SYSTEM,
        variables=['instruction', 'json_contract'],
        role='system',
    )),
    ('PDF_GENERIC_BOX_USER', _prompt(
        group='PDF Batch Import — Generic Extraction',
        label='PDF generic: User-turn instruction',
        description=(
            "Accompanies the single page image for Generic Extraction. "
            "{{instruction}} is the user request; {{box_pairs}} is filled from "
            "the PDF_IMPORT_COORD_ORDER setting (xyxy vs yxyx). ALSO powers "
            "the Toolbox → PDF Tool → Find & Mark \"AI detect\" engine."
        ),
        default=_DEFAULT_PDF_GENERIC_BOX_USER,
        variables=['instruction', 'box_pairs'],
        role='user',
        format_key='PDF_GENERIC_BOX_JSON_CONTRACT',
    )),
    ('PDF_ANCHOR_JSON_CONTRACT', _prompt(
        group='PDF Batch Import — Assisted (Anchor) Detection',
        label='PDF anchor: JSON contract (shared)',
        description=(
            'Response contract for the "segment" assisted method: the model '
            'returns only {qno, y-start} per question and classical CV derives '
            'the boxes. Substituted into the anchor system prompt via '
            '{{json_contract}}. Parser parse_question_anchors is coupled to '
            'this shape.'
        ),
        default=_DEFAULT_PDF_ANCHOR_JSON_CONTRACT,
        role='format',
    )),
    ('PDF_ANCHOR_SYSTEM', _prompt(
        group='PDF Batch Import — Assisted (Anchor) Detection',
        label='PDF anchor: System prompt',
        description=(
            'Used by the "segment" assisted method to find where each question '
            '/ solution starts (one y per item). {{what}} is "question" or '
            '"solution"; {{json_contract}} is replaced with the shared anchor '
            'contract above.'
        ),
        default=_DEFAULT_PDF_ANCHOR_SYSTEM,
        variables=['what', 'json_contract'],
        role='system',
    )),
    ('PDF_ANCHOR_USER', _prompt(
        group='PDF Batch Import — Assisted (Anchor) Detection',
        label='PDF anchor: User-turn instruction',
        description=(
            "Accompanies the single page image for the anchor method. {{what}} "
            "is 'questions' or 'solutions'."
        ),
        default=_DEFAULT_PDF_ANCHOR_USER,
        variables=['what'],
        role='user',
        format_key='PDF_ANCHOR_JSON_CONTRACT',
    )),
    ('PDF_PAPER_NAME_SYSTEM', _prompt(
        group='PDF Batch Import — Paper-name Guess',
        label='Paper-name guess: System prompt',
        description=(
            'Used to auto-fill the Paper name field in PDF Batch Import. The '
            'model is shown the first page image + file name and must reply '
            'with STRICT JSON {paper, confidence}. The parser '
            '(parse_paper_name) and the SUBJECT_SOURCE_YEAR_PAPER contract '
            'are coupled to this prompt — keep the JSON shape.'
        ),
        default=_DEFAULT_PDF_PAPER_NAME_SYSTEM,
        role='system',
    )),
    ('PDF_PAPER_NAME_USER', _prompt(
        group='PDF Batch Import — Paper-name Guess',
        label='Paper-name guess: User-turn instruction',
        description=(
            'Accompanies the first-page image. {{filename}} is the PDF file '
            'name; {{subjects}} is the comma-separated list of subject codes '
            'the user may import into.'
        ),
        default=_DEFAULT_PDF_PAPER_NAME_USER,
        variables=['filename', 'subjects'],
        role='user',
    )),
])


def build_figure_box_system(coord_order: str = 'xyxy', endpoint_id=None) -> str:
    """Resolved system prompt for figure localisation during MD generation.
    ``coord_order`` (the ``PDF_IMPORT_COORD_ORDER`` setting) drives the
    coordinate-order wording in the contract so the prompt matches what
    ``parse_figure_boxes`` expects."""
    contract = render_prompt('FIGURE_BOX_JSON_CONTRACT', endpoint_id=endpoint_id,
                             **pdf_box_order_vars(coord_order))
    return render_prompt('FIGURE_BOX_SYSTEM', endpoint_id=endpoint_id,
                         json_contract=contract)


def build_figure_box_user_text(coord_order: str = 'xyxy', endpoint_id=None) -> str:
    """User-turn instruction for figure localisation during MD generation,
    with the JSON contract re-appended for emphasis. ``coord_order`` (the
    ``PDF_IMPORT_COORD_ORDER`` setting) fills the axis-order wording so the
    instruction matches the parser."""
    order_vars = pdf_box_order_vars(coord_order)
    text = render_prompt('FIGURE_BOX_USER', endpoint_id=endpoint_id, **order_vars)
    return append_format('FIGURE_BOX_USER', text, endpoint_id=endpoint_id,
                         **order_vars)


def pdf_box_order_vars(coord_order: str = 'xyxy') -> dict:
    """Order-specific text fragments for the PDF bbox prompts, so the prompt
    INSTRUCTION matches the ``PDF_IMPORT_COORD_ORDER`` setting instead of
    hardcoding x-first. Keys: ``box_array`` (the ``"box"`` array shape),
    ``box_corner`` (the corner/ordering sentence), ``box_example`` (the worked
    example array), ``box_pairs`` (the user-turn ``[..] = [..]`` mapping)."""
    if (coord_order or 'xyxy').strip().lower() == 'yxyx':
        return {
            'box_array': '[y1, x1, y2, x2]',
            'box_corner': (
                '[y1, x1, y2, x2] — the VERTICAL coordinate comes FIRST — '
                'where (y1,x1) is its TOP-LEFT corner and (y2,x2) its '
                'BOTTOM-RIGHT corner, so always y1 < y2 and x1 < x2'),
            'box_example': '[70, 40, 330, 960]',
            'box_pairs': '[y1, x1, y2, x2] = [top, left, bottom, right]',
        }
    return {
        'box_array': '[x1, y1, x2, y2]',
        'box_corner': (
            '[x1, y1, x2, y2] where (x1,y1) is its TOP-LEFT corner and '
            '(x2,y2) its BOTTOM-RIGHT corner, so always x1 < x2 and y1 < y2'),
        'box_example': '[40, 70, 960, 330]',
        'box_pairs': '[x1, y1, x2, y2] = [left, top, right, bottom]',
    }


def build_pdf_box_user_text(asset_type: str, coord_order: str = 'xyxy',
                            endpoint_id=None) -> str:
    """User-turn instruction accompanying a single page image, with the
    shared JSON contract re-appended for emphasis. ``coord_order`` (the
    ``PDF_IMPORT_COORD_ORDER`` setting) fills the axis-order wording."""
    what = 'questions' if asset_type == 'QUE' else 'solutions'
    order_vars = pdf_box_order_vars(coord_order)
    text = render_prompt('PDF_BOX_USER', endpoint_id=endpoint_id, what=what,
                         **order_vars)
    return append_format('PDF_BOX_USER', text, endpoint_id=endpoint_id,
                         **order_vars)


def build_pdf_box_system(asset_type: str, coord_order: str = 'xyxy',
                         endpoint_id=None) -> str:
    """Resolved system prompt for the PDF page-detection model, with the
    shared JSON contract substituted in. Use this from call sites instead of
    branching on QUE/SOL yourself. ``coord_order`` (the
    ``PDF_IMPORT_COORD_ORDER`` setting) drives the coordinate-order wording in
    the contract so the prompt matches what ``parse_question_boxes`` expects."""
    contract = render_prompt('PDF_BOX_JSON_CONTRACT', endpoint_id=endpoint_id,
                             **pdf_box_order_vars(coord_order))
    key = 'PDF_QUE_BOX_SYSTEM' if asset_type == 'QUE' else 'PDF_SOL_BOX_SYSTEM'
    return render_prompt(key, endpoint_id=endpoint_id, json_contract=contract)


def build_pdf_generic_system(instruction: str, coord_order: str = 'xyxy',
                             endpoint_id=None) -> str:
    """Resolved system prompt for Generic Extraction (no exam context).
    ``instruction`` is the user's free-text request; ``coord_order`` (the
    PDF_IMPORT_COORD_ORDER setting) drives the coordinate-order wording."""
    contract = render_prompt('PDF_GENERIC_BOX_JSON_CONTRACT',
                             endpoint_id=endpoint_id,
                             **pdf_box_order_vars(coord_order))
    return render_prompt('PDF_GENERIC_BOX_SYSTEM',
                         endpoint_id=endpoint_id,
                         instruction=(instruction or '').strip()
                         or '(no specific request given — extract the main content regions)',
                         json_contract=contract)


def build_pdf_generic_user_text(instruction: str, coord_order: str = 'xyxy',
                                endpoint_id=None) -> str:
    """User-turn instruction for Generic Extraction accompanying one page,
    with the generic JSON contract re-appended for emphasis."""
    order_vars = pdf_box_order_vars(coord_order)
    text = render_prompt('PDF_GENERIC_BOX_USER',
                         endpoint_id=endpoint_id,
                         instruction=(instruction or '').strip()
                         or 'Extract the main content regions',
                         **order_vars)
    return append_format('PDF_GENERIC_BOX_USER', text, endpoint_id=endpoint_id,
                         **order_vars)


def build_pdf_anchor_user_text(asset_type: str, endpoint_id=None) -> str:
    """User-turn instruction for the anchor (segment) detection method, with
    the anchor JSON contract re-appended for emphasis."""
    what = 'questions' if asset_type == 'QUE' else 'solutions'
    text = render_prompt('PDF_ANCHOR_USER', endpoint_id=endpoint_id, what=what)
    return append_format('PDF_ANCHOR_USER', text, endpoint_id=endpoint_id)


def build_pdf_anchor_system(asset_type: str, endpoint_id=None) -> str:
    """Resolved anchor-detection system prompt (segment method), with the
    shared anchor JSON contract substituted in."""
    contract = get_prompt('PDF_ANCHOR_JSON_CONTRACT', endpoint_id)
    what = 'question' if asset_type == 'QUE' else 'solution'
    return render_prompt('PDF_ANCHOR_SYSTEM', endpoint_id=endpoint_id,
                         what=what, json_contract=contract)


def build_pdf_paper_name_system(endpoint_id=None) -> str:
    """System prompt for the PDF Import paper-name auto-guess."""
    return get_prompt('PDF_PAPER_NAME_SYSTEM', endpoint_id)


def build_pdf_paper_name_user_text(filename: str, subjects,
                                   endpoint_id=None) -> str:
    """User-turn instruction for the paper-name guess. ``subjects`` is an
    iterable of allowed subject codes (or a string)."""
    if isinstance(subjects, (list, tuple, set)):
        subjects = ', '.join(str(s) for s in subjects) or '(none configured)'
    return render_prompt('PDF_PAPER_NAME_USER', endpoint_id=endpoint_id,
                         filename=filename or '(unknown)',
                         subjects=subjects)


def parse_paper_name(text: str):
    """Extract the guessed ``SUBJECT_SOURCE_YEAR_PAPER`` code from the model's
    reply. Returns ``(paper_or_None, confidence_float)``. Tolerant of markdown
    fences and surrounding prose — finds the first JSON object with a "paper"
    key, falling back to a regex scan for the code pattern."""
    if not text:
        return None, 0.0
    cleaned = strip_md_fences(text)

    paper = None
    confidence = 0.0
    # Try every {...} object until one parses with a usable "paper".
    for m in re.finditer(r'\{[^{}]*\}', cleaned, re.DOTALL):
        try:
            obj = json.loads(m.group(0))
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and 'paper' in obj:
            paper = obj.get('paper')
            try:
                confidence = float(obj.get('confidence', 0) or 0)
            except (ValueError, TypeError):
                confidence = 0.0
            break

    if not paper:
        # Fall back to a direct pattern scan over the whole reply.
        m = re.search(r'\b([A-Z0-9]+_(?:DSE|CE|AL)_\d{4}_P[A-Za-z0-9]+)\b',
                      cleaned.upper())
        if m:
            return m.group(1), confidence

    if isinstance(paper, str):
        paper = paper.strip().strip('"').upper()
        return (paper or None), confidence
    return None, confidence


def _normalize_scalar(v, dim=None):
    """Normalise a single coordinate to a 0..1 fraction, mirroring the range
    logic in :func:`_normalize_box` (0..1 kept; <=1024 treated as 0..1000;
    larger treated as pixels and divided by ``dim`` when known, else /1000)."""
    v = abs(float(v))
    if v <= 1.0:
        f = v
    elif v <= 1024.0:
        f = v / 1000.0
    elif dim:
        f = v / float(dim)
    else:
        f = v / 1000.0
    return min(max(f, 0.0), 1.0)


def parse_question_anchors(text: str, img_h=None, coord_order='xyxy'):
    """Parse the anchor-detection model output into a list of
    ``{qno:int|None, y:float}`` (fractional y).

    Tolerant of code fences / surrounding prose (mirrors
    ``parse_question_boxes``). ``coord_order`` is accepted for signature parity
    but a single y has no axis ambiguity, so it's ignored. Accepts ``y`` (or
    ``top`` / ``y1``) and, as a fallback, the ``y1`` of a ``box``. Returns ``[]``
    on total failure."""
    if not text:
        return []
    for c in _json_candidates(text, '['):
        try:
            data = json.loads(c)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            data = (data.get('questions') or data.get('anchors')
                    or data.get('regions') or [])
        if not isinstance(data, list):
            continue
        out = []
        for it in data:
            if not isinstance(it, dict):
                continue
            yraw = it.get('y', it.get('top', it.get('y1')))
            if yraw is None:
                box = it.get('box') or it.get('bbox')
                if isinstance(box, (list, tuple)) and len(box) >= 2:
                    yraw = box[1]
            if yraw is None:
                continue
            try:
                yv = _normalize_scalar(yraw, img_h)
            except (ValueError, TypeError):
                continue
            qno_raw = it.get('qno', it.get('question_number', it.get('number')))
            qno = None
            if qno_raw is not None:
                mqn = re.search(r'\d+', str(qno_raw))
                if mqn:
                    qno = int(mqn.group(0))
            out.append({'qno': qno, 'y': yv})
        return out
    return []


def _normalize_box(coords, img_w=None, img_h=None, coord_order='xyxy'):
    """Normalise a raw 4-tuple model box to fractional ``[x1,y1,x2,y2]`` in
    0..1, handling axis order and the common number ranges.

    ``coord_order``:
      * ``'xyxy'`` (default) — ``[x1, y1, x2, y2]`` (Qwen, most models).
      * ``'yxyx'`` — ``[y1, x1, y2, x2]`` (Gemma / Gemini / PaliGemma family);
        re-ordered to x-first here.

    Range detection (after axis re-ordering):
      * max <= 1   → already fractional (0..1).
      * max <= 1024 → normalised 0..1000/0..1024 integers → divide by 1000.
      * else        → raw pixels → divide by the supplied image dims (the
        downscaled size the model actually saw) when known, else by the max.
    """
    if (coord_order or 'xyxy').lower() == 'yxyx':
        # [y1, x1, y2, x2] -> [x1, y1, x2, y2]
        coords = [coords[1], coords[0], coords[3], coords[2]]
    mx = max((abs(v) for v in coords), default=0.0)
    if mx <= 1.0:
        pass  # already fractional
    elif mx <= 1024.0:
        coords = [v / 1000.0 for v in coords]
    elif img_w and img_h:
        coords = [coords[0] / img_w, coords[1] / img_h,
                  coords[2] / img_w, coords[3] / img_h]
    else:
        coords = [v / mx for v in coords]
    return [min(max(v, 0.0), 1.0) for v in coords]


def parse_question_boxes(text: str, img_w=None, img_h=None, coord_order='xyxy'):
    """Parse the page-detection model output into a list of
    ``{qno, box:[x1,y1,x2,y2], continues_prev, continues_next}``.

    Tolerant of code fences and surrounding prose (mirrors
    ``parse_figure_boxes``). Coordinates are normalised + clamped to 0..1 via
    :func:`_normalize_box` (honouring ``coord_order`` and ``img_w``/``img_h``).
    ``qno`` is coerced to an int when possible, else ``None``. Returns ``[]``
    on total failure.
    """
    if not text:
        return []

    def _emit(items):
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            box = it.get('box') or it.get('bbox') or it.get('bounding_box')
            if not (isinstance(box, (list, tuple)) and len(box) == 4):
                continue
            try:
                coords = [float(v) for v in box]
            except (ValueError, TypeError):
                continue
            x1, y1, x2, y2 = _normalize_box(coords, img_w, img_h, coord_order)
            qno_raw = it.get('qno', it.get('question_number', it.get('number')))
            qno = None
            if qno_raw is not None:
                mqn = re.search(r'\d+', str(qno_raw))
                if mqn:
                    qno = int(mqn.group(0))
            out.append({
                'qno': qno,
                'box': [x1, y1, x2, y2],
                'continues_prev': bool(it.get('continues_prev', False)),
                'continues_next': bool(it.get('continues_next', False)),
            })
        return out

    for c in _json_candidates(text, '['):
        try:
            data = json.loads(c)
        except (ValueError, TypeError):
            continue
        items = _as_item_list(data, ('questions', 'boxes', 'regions'))
        if items is None:
            continue
        parsed = _emit(items)
        if parsed:
            return parsed

    # Strict parse failed (or yielded nothing). Salvage individual objects from
    # malformed JSON so one corrupt object can't drop the whole page.
    out = []
    for raw, box in _salvage_box_objects(text):
        x1, y1, x2, y2 = _normalize_box(box, img_w, img_h, coord_order)
        out.append({
            'qno': _salvage_int(raw, 'qno', 'question_number', 'number'),
            'box': [x1, y1, x2, y2],
            'continues_prev': _salvage_bool(raw, 'continues_prev'),
            'continues_next': _salvage_bool(raw, 'continues_next'),
        })
    return out


def parse_generic_boxes(text: str, img_w=None, img_h=None, coord_order='xyxy'):
    """Parse the Generic Extraction model output into a list of
    ``{label, box:[x1,y1,x2,y2]}``.

    Mirrors :func:`parse_question_boxes` (same fence/array tolerance and
    coordinate normalisation) but reads a free-text ``label`` instead of a
    printed question number. ``label`` falls back to ``None`` when absent.
    """
    if not text:
        return []

    def _emit(items):
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            box = it.get('box') or it.get('bbox') or it.get('bounding_box')
            if not (isinstance(box, (list, tuple)) and len(box) == 4):
                continue
            try:
                coords = [float(v) for v in box]
            except (ValueError, TypeError):
                continue
            x1, y1, x2, y2 = _normalize_box(coords, img_w, img_h, coord_order)
            label_raw = it.get('label', it.get('name', it.get('title')))
            label = None
            if label_raw is not None and str(label_raw).strip() != '':
                label = str(label_raw).strip()[:80]
            out.append({'label': label, 'box': [x1, y1, x2, y2]})
        return out

    for c in _json_candidates(text, '['):
        try:
            data = json.loads(c)
        except (ValueError, TypeError):
            continue
        items = _as_item_list(data, ('regions', 'boxes', 'items'))
        if items is None:
            continue
        parsed = _emit(items)
        if parsed:
            return parsed

    out = []
    for raw, box in _salvage_box_objects(text):
        x1, y1, x2, y2 = _normalize_box(box, img_w, img_h, coord_order)
        lbl = _salvage_str(raw, 'label', 'name', 'title')
        out.append({'label': lbl[:80] if lbl else None, 'box': [x1, y1, x2, y2]})
    return out


def strip_md_fences(text: str) -> str:
    """Remove a single wrapping ```...``` fence if the model added one
    around the whole answer despite instructions."""
    s = (text or '').strip()
    m = re.match(r'^```[a-zA-Z0-9_-]*\s*\n(.*)\n```$', s, re.DOTALL)
    if m:
        return m.group(1).strip()
    return s


# Tighten padded inline math delimiters: "$ x $" -> "$x$".
#
# Pandoc's `tex_math_dollars` extension (the .docx path) and standard
# dollar-math only recognise inline math when the opening "$" is NOT followed
# by whitespace and the closing "$" is NOT preceded by whitespace. LLMs often
# emit "$ x $" anyway, which then renders as literal text in the generated
# Word doc. We strip that inner padding so the same content renders in the
# editor preview, the dashboard, AND the .docx. Display math ($$...$$),
# escaped "\$", and currency ("$5") are left untouched.
_INLINE_MATH_PAD_RE = re.compile(r'(?<![\\$])\$[ \t]*([^\n$]*?[^\s$])[ \t]*\$(?!\d)')


def normalize_inline_math(text: str) -> str:
    """Return ``text`` with the inner padding of inline ``$ ... $`` math
    removed so it parses everywhere. Safe to call on any Markdown (no-op when
    there is no padded inline math)."""
    if not text or '$' not in text:
        return text
    return _INLINE_MATH_PAD_RE.sub(lambda m: '$' + m.group(1) + '$', text)


# ==================== Robust JSON extraction ====================

def parse_check_result(text: str):
    """Parse the proofreading model output into a normalised dict:
    ``{status: 'ok'|'issues', issues: [...]}``.

    Tolerant of code fences and surrounding prose. On total failure returns
    ``None`` so the caller can fall back to storing the raw text.
    """
    if not text:
        return None
    for c in _json_candidates(text, '{'):
        try:
            data = json.loads(c)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        status = str(data.get('status', '')).lower().strip()
        if status not in ('ok', 'issues'):
            # infer from presence of issues
            raw_issues = data.get('issues') or []
            status = 'issues' if raw_issues else 'ok'
        issues = []
        for it in (data.get('issues') or []):
            if isinstance(it, dict):
                issues.append({
                    'location': str(it.get('location', '') or ''),
                    'description': str(it.get('description', '') or ''),
                    'severity': str(it.get('severity', '') or 'minor').lower(),
                })
            elif it:
                issues.append({'location': '', 'description': str(it),
                               'severity': 'minor'})
        if status == 'issues' and not issues:
            # model said issues but gave none — treat as ok to avoid noise
            status = 'ok'
        return {'status': status, 'issues': issues}
    return None
