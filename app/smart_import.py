"""
Smart Import engine — heuristic folder matching + format-aware apply.

This module powers the **Folder import** mode of the unified Smart Import tool
(Library scan mode still lives in :mod:`app.ingestor`). It takes an arbitrary
server folder — even a messy dump like ``2012/P1/Q9.png`` — and works out which
existing question + asset slot each file belongs to, so an admin can review
old-vs-new and apply create/overwrite in bulk.

Two phases:

1. :func:`resolve_folder` — walk a folder and emit a ``proposal`` per file:
   strict canonical filename first (same parser as ingestion), then a
   heuristic path/token scan, with the missing dimensions (subject / source /
   version / asset_type / detail) filled from a per-import **profile**. Each
   proposal carries a status (``overwrite`` / ``add`` / ``unmatched`` /
   ``ambiguous`` / ``skip``), a confidence, and the existing slot files for the
   compare view.

2. :func:`iter_apply` — a generator (SSE-friendly) that, per accepted job,
   copies the file into its canonical ``SOURCE_PATH`` location and writes the
   ``QuestionAsset`` row, format-aware:
     * IMG -> :func:`app.batch_image_gen.replace_img_assets` (whole-slot replace)
     * DOC -> delete old DOC row+file, copy ``.docx``, ``on_doc_asset_created``
     * MD  -> write canonical ``.md``, upsert row, ``md_render.invalidate``

The deterministic engine is the source of truth; the optional LLM assist only
refines the *profile / structure rule* and then this same code re-runs.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
import logging
from datetime import datetime

from flask import current_app

from app import db
from app import storage
from app import md_render
from app.models import Question, QuestionAsset
from app.utils import VERSIONS
from app.ingestor import (
    parse_filename, construct_qid, determine_file_format,
    determine_question_type, parse_qno,
)

logger = logging.getLogger(__name__)

# Asset-type / source vocab shared with the rest of the app.
ASSET_TYPES = ('QUE', 'ANS', 'SOL')
PP_SOURCES = ('DSE', 'CE', 'AL')
ALL_SOURCES = PP_SOURCES + ('QB',)
IMG_EXTS = ('png', 'jpg', 'jpeg', 'gif', 'bmp')

# ---------------------------------------------------------------------------
# Token extraction (heuristic mode)
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r'^(?:19|20)\d{2}$')
_PAPER_RE = re.compile(r'^P\d{1,3}[A-Za-z]?$', re.IGNORECASE)
_QNO_RE = re.compile(r'Q?0*(\d{1,3})$', re.IGNORECASE)
_VERSION_RE = re.compile(r'^(ENO|CHO|EN|CH|BI)$', re.IGNORECASE)
_TYPE_RE = re.compile(r'^(QUE|ANS|SOL)$', re.IGNORECASE)
# Split a path segment / filename stem into candidate tokens.
_SPLIT_RE = re.compile(r'[\s._\-]+')


def _tokenize(text):
    return [t for t in _SPLIT_RE.split(text) if t]


def _scan_tokens(segments, stem):
    """Scan path segments (folders) + the filename stem for the dimensions we
    can recover heuristically. Returns a dict with any of:
    ``year, paper, qno, version, asset_type`` (only keys actually found).

    Preference rules:
      * ``qno`` is taken from the **filename stem** first (e.g. ``Q9``), then
        from folder segments.
      * ``year`` / ``paper`` are taken from folder segments first (the common
        ``<year>/<paper>/`` layout), then the stem.
      * ``version`` / ``asset_type`` from any token.
    """
    found = {}

    stem_tokens = _tokenize(stem)
    seg_tokens = []
    for seg in segments:
        seg_tokens.extend(_tokenize(seg))

    # qno: filename stem wins. Accept a bare number or Q<number>.
    for tok in stem_tokens + seg_tokens:
        m = _QNO_RE.match(tok)
        if m:
            found['qno'] = int(m.group(1))
            break

    # year / paper: folders first, then stem.
    for tok in seg_tokens + stem_tokens:
        if 'year' not in found and _YEAR_RE.match(tok):
            found['year'] = int(tok)
        if 'paper' not in found and _PAPER_RE.match(tok):
            found['paper'] = tok.upper()

    # version / asset_type: any token.
    for tok in seg_tokens + stem_tokens:
        if 'version' not in found and _VERSION_RE.match(tok):
            found['version'] = tok.upper()
        if 'asset_type' not in found and _TYPE_RE.match(tok):
            found['asset_type'] = tok.upper()

    return found


def _format_from_ext(ext):
    return determine_file_format(ext)


# ---------------------------------------------------------------------------
# Profile normalisation
# ---------------------------------------------------------------------------

def normalize_profile(raw):
    """Coerce a client-supplied profile dict into a clean, validated profile.

    All fields have safe defaults so a half-filled form still resolves.
    """
    raw = raw or {}

    def _up(key, default=''):
        return str(raw.get(key, default) or '').strip().upper()

    subject = _up('subject')
    source = _up('source') or 'DSE'
    if source not in ALL_SOURCES:
        source = 'DSE'
    version = _up('version') or 'EN'
    if version not in VERSIONS:
        version = 'EN'
    asset_type = _up('asset_type') or 'QUE'
    if asset_type not in ASSET_TYPES:
        asset_type = 'QUE'
    detail = str(raw.get('detail', '') or '').strip()

    def _bool(key, default):
        v = raw.get(key, default)
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ('1', 'true', 'yes', 'on')

    rule = raw.get('rule') if isinstance(raw.get('rule'), dict) else {}

    return {
        'subject': subject,
        'source': source,
        'detail': detail,
        'version': version,
        'asset_type': asset_type,
        'overwrite': _bool('overwrite', True),
        'backup': _bool('backup', False),
        'create_missing': _bool('create_missing', False),
        'rule': rule,
    }


def _apply_rule_to_profile(profile):
    """Fold an optional structure ``rule`` (e.g. from the AI assist) into the
    profile defaults. The rule may override the default version / asset_type /
    source / subject / detail. Token scanning still recovers year/paper/qno."""
    rule = profile.get('rule') or {}
    out = dict(profile)
    for key in ('subject', 'source', 'version', 'asset_type', 'detail'):
        val = rule.get(key)
        if not val:
            continue
        val = str(val).strip()
        if key in ('subject', 'source', 'version', 'asset_type'):
            val = val.upper()
        if key == 'source' and val not in ALL_SOURCES:
            continue
        if key == 'version' and val not in VERSIONS:
            continue
        if key == 'asset_type' and val not in ASSET_TYPES:
            continue
        out[key] = val
    return out


# ---------------------------------------------------------------------------
# Optional AI structure inference
# ---------------------------------------------------------------------------

def list_sample_paths(base_dir, rel_path, limit=80):
    """Return up to ``limit`` relative file paths under the folder (forward
    slashes), for previewing / feeding the AI assist."""
    root = os.path.abspath(base_dir)
    start = storage.safe_join(root, rel_path) if rel_path else root
    out = []
    if not start or not os.path.isdir(start):
        return out
    for cur, dirs, files in os.walk(start):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(cur, name)
            out.append(os.path.relpath(full, start).replace('\\', '/'))
            if len(out) >= limit:
                return out
    return out


def _parse_json_object(text):
    """Best-effort extraction of a single JSON object from an LLM reply."""
    if not text:
        return None
    s = text.strip()
    if s.startswith('```'):
        s = s.strip('`')
        if '\n' in s:
            s = s.split('\n', 1)[1]
    # Grab the outermost {...}.
    start = s.find('{')
    end = s.rfind('}')
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(s[start:end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def infer_structure_rule(base_dir, rel_path, profile, config):
    """Ask an LLM to infer folder-level defaults (a structure ``rule``).

    Returns ``(rule, raw_text)``. ``rule`` is a dict suitable for
    ``profile['rule']`` (subject/source/version/asset_type/detail) plus a
    ``notes`` string. Raises ``RuntimeError`` on transport failure so the route
    can surface a clean message. Prompts are editable on Admin -> AI Prompts
    (``SMART_IMPORT_SYSTEM`` / ``SMART_IMPORT_USER``)."""
    from app import llm_client
    from app import ai_prompts

    profile = normalize_profile(profile)
    samples = list_sample_paths(base_dir, rel_path, limit=80)
    if not samples:
        raise RuntimeError('No files found in the selected folder.')

    tree = '\n'.join(samples[:80])
    system = ai_prompts.system_prompt('SMART_IMPORT_SYSTEM', config.id)
    user = ai_prompts.render_prompt(
        'SMART_IMPORT_USER', endpoint_id=config.id,
        subject=(profile.get('subject') or '(unknown)'),
        versions=', '.join(VERSIONS), tree=tree)
    try:
        text, _info = llm_client.chat(config, system, user)
    except Exception as e:
        raise RuntimeError(f'LLM request failed: {e}')

    obj = _parse_json_object(text)
    if obj is None:
        raise RuntimeError('Could not parse a structure rule from the AI reply.')

    rule = {}
    for key in ('subject', 'source', 'version', 'asset_type', 'detail', 'notes'):
        val = obj.get(key)
        if val:
            rule[key] = str(val).strip()
    return rule, text


# ---------------------------------------------------------------------------
# QID construction (heuristic)
# ---------------------------------------------------------------------------

def _build_qid(subject, source, year, paper, detail, qno):
    """Build a QID from resolved components, or return ``None`` when a required
    piece is missing for the chosen source."""
    if not subject or qno is None:
        return None
    if source in PP_SOURCES:
        if year is None or not paper:
            return None
        return f"{subject}_{source}_{year}_{paper}_Q{qno}"
    # QB
    if not detail:
        return None
    return f"{subject}_QB_{detail}_Q{qno}"


# ---------------------------------------------------------------------------
# Resolve one file -> proposal
# ---------------------------------------------------------------------------

def _issue_text(check_state, check_result):
    """Render an existing asset's proofread issues into a short human string
    for the compare view. Returns '' when there's nothing to show."""
    if not check_result:
        return ''
    try:
        data = json.loads(check_result)
    except (ValueError, TypeError):
        return check_result if check_state in ('issues', 'error') else ''
    issues = data.get('issues') if isinstance(data, dict) else None
    if not issues:
        return ''
    lines = []
    for it in issues:
        if isinstance(it, dict):
            sev = (it.get('severity') or '').strip()
            loc = (it.get('location') or '').strip()
            desc = (it.get('description') or it.get('issue') or '').strip()
            prefix = ' '.join(p for p in [f'[{sev}]' if sev else '', f'{loc}:' if loc else ''] if p)
            lines.append((prefix + ' ' + desc).strip())
        elif it:
            lines.append(str(it))
    return '\n'.join(l for l in lines if l)


def _existing_slot_assets(question, asset_type, version, file_format):
    """Existing assets in the target slot, as lightweight dicts for the
    compare view (ordered by part), including any proofread issue message."""
    rows = (QuestionAsset.query
            .filter_by(question_id=question.id, asset_type=asset_type,
                       version=version, file_format=file_format)
            .order_by(QuestionAsset.part_number).all())
    out = []
    for r in rows:
        out.append({
            'asset_id': r.id,
            'file_path': r.file_path,
            'part_number': r.part_number,
            'file_format': r.file_format,
            'check_state': r.check_state,
            'issue_text': _issue_text(r.check_state, r.check_result),
        })
    return out


def _resolve_file(rel_path, filename, profile):
    """Resolve a single file (relative to the import folder) to a proposal
    dict. ``rel_path`` uses forward slashes and INCLUDES the filename."""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    file_format = _format_from_ext(ext)

    base = {
        'src_rel': rel_path,
        'filename': filename,
        'ext': ext,
        'format': file_format,
        'subject': profile['subject'],
        'source': profile['source'],
        'detail': profile.get('detail', ''),
        'year': None,
        'paper': None,
        'qno': None,
        'version': profile['version'],
        'asset_type': profile['asset_type'],
        'part': 1,
        'method': None,
        'confidence': 'none',
        'status': 'skip',
        'qid': None,
        'note': '',
        'existing': [],
        'existing_count': 0,
        'accept': False,
    }

    if not file_format:
        base['note'] = f'Unsupported file type (.{ext})' if ext else 'No file extension'
        return base

    # 1) Strict canonical filename — same parser as ingestion.
    parsed = parse_filename(filename)
    if parsed:
        base['method'] = 'strict'
        base['confidence'] = 'high'
        base['subject'] = parsed['subj']
        base['source'] = parsed['source']
        base['version'] = parsed['version']
        base['asset_type'] = parsed['type']
        base['part'] = parsed.get('part', 1)
        if parsed['source'] in PP_SOURCES:
            base['year'] = int(parsed['year'])
            base['paper'] = parsed['paper']
        else:
            base['detail'] = parsed.get('detail', '')
        base['qno'] = parse_qno(parsed['qno'])
        base['qid'] = construct_qid(parsed)
    else:
        # 2) Heuristic path/token scan + profile defaults.
        base['method'] = 'heuristic'
        segments = rel_path.split('/')[:-1]  # folders only
        stem = filename.rsplit('.', 1)[0]
        found = _scan_tokens(segments, stem)

        base['year'] = found.get('year')
        base['paper'] = found.get('paper')
        base['qno'] = found.get('qno')
        if found.get('version'):
            base['version'] = found['version']
        if found.get('asset_type'):
            base['asset_type'] = found['asset_type']

        base['qid'] = _build_qid(base['subject'], base['source'], base['year'],
                                 base['paper'], base['detail'], base['qno'])

        # Confidence: how many key dimensions came from the data vs guessed.
        if base['qno'] is None:
            base['confidence'] = 'none'
        elif base['source'] in PP_SOURCES:
            have = (base['year'] is not None) + bool(base['paper'])
            if have == 2:
                base['confidence'] = 'high' if (found.get('version') or found.get('asset_type')) else 'medium'
            elif have == 1:
                base['confidence'] = 'low'
            else:
                base['confidence'] = 'low'
        else:  # QB
            base['confidence'] = 'medium' if base['detail'] else 'low'

    # Classify against the DB.
    if not base['qid']:
        base['status'] = 'skip'
        base['note'] = base['note'] or 'Could not determine a question number / QID from the path.'
        return base

    if not base['subject']:
        base['status'] = 'ambiguous'
        base['note'] = 'No subject set — choose a subject in the profile.'
        return base

    question = Question.query.filter_by(qid=base['qid']).first()
    if question is None:
        base['status'] = 'unmatched'
        base['note'] = f'No question {base["qid"]} in the database.'
        base['accept'] = bool(profile.get('create_missing'))
        return base

    existing = _existing_slot_assets(question, base['asset_type'], base['version'], file_format)
    base['existing'] = existing
    base['existing_count'] = len(existing)

    if base['confidence'] == 'low':
        base['status'] = 'ambiguous'
        base['note'] = 'Low confidence — please confirm the QID / slot.'
        base['accept'] = False
        return base

    if existing:
        base['status'] = 'overwrite'
        base['note'] = f'{len(existing)} existing {file_format} file(s) in {base["asset_type"]}/{base["version"]} will be replaced.'
        base['accept'] = bool(profile.get('overwrite'))
    else:
        base['status'] = 'add'
        base['note'] = f'New {file_format} into empty {base["asset_type"]}/{base["version"]} slot.'
        base['accept'] = True

    return base


def resolve_folder(base_dir, rel_path, profile, qid_scope=None):
    """Walk ``base_dir/rel_path`` and resolve every file to a proposal.

    Args:
        base_dir: absolute path of the resolved Root.
        rel_path: relative folder within the root (forward slashes).
        profile: normalised profile dict (see :func:`normalize_profile`).
        qid_scope: optional set/list of QIDs to restrict matching to. Files
            resolving outside the scope are flagged ``skip`` with a note.

    Returns ``{proposals: [...], stats: {...}, folder: rel_path}``.
    """
    profile = _apply_rule_to_profile(normalize_profile(profile))
    scope = set(qid_scope) if qid_scope else None

    root = os.path.abspath(base_dir)
    start = storage.safe_join(root, rel_path) if rel_path else root
    proposals = []

    if not start or not os.path.isdir(start):
        return {'proposals': [], 'stats': _empty_stats(), 'folder': rel_path,
                'error': 'Folder not found or access denied.'}

    for cur, dirs, files in os.walk(start):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(cur, name)
            rel = os.path.relpath(full, root).replace('\\', '/')
            prop = _resolve_file(rel, name, profile)
            if scope is not None and prop.get('qid') and prop['qid'] not in scope:
                if prop['status'] not in ('skip',):
                    prop['status'] = 'skip'
                    prop['accept'] = False
                    prop['note'] = f'{prop["qid"]} is outside the selected questions.'
            proposals.append(prop)

    # Stable row ids for the review grid.
    for i, p in enumerate(proposals):
        p['id'] = i

    return {
        'proposals': proposals,
        'stats': _summarize(proposals),
        'folder': rel_path,
        'profile': profile,
    }


def _empty_stats():
    return {'total': 0, 'overwrite': 0, 'add': 0, 'unmatched': 0,
            'ambiguous': 0, 'skip': 0, 'accepted': 0}


def _summarize(proposals):
    stats = _empty_stats()
    stats['total'] = len(proposals)
    for p in proposals:
        stats[p['status']] = stats.get(p['status'], 0) + 1
        if p.get('accept'):
            stats['accepted'] += 1
    return stats


# ---------------------------------------------------------------------------
# Canonical destination path
# ---------------------------------------------------------------------------

def _canonical_rel(subject, source, year, paper, detail, qid, version, asset_type, ext, part=1):
    """Canonical relative file path under SOURCE_PATH (forward slashes),
    mirroring admin._build_asset_file_path / upload_question_asset."""
    part_suffix = f'_{part}' if part and part > 1 else ''
    filename = f"{qid}_{version}_{asset_type}{part_suffix}.{ext}"
    if source in PP_SOURCES:
        folder = '/'.join([subject, 'PP', source, str(year), paper])
    else:
        folder = '/'.join([subject, 'QB', detail or 'UNKNOWN'])
    return f"{folder}/{filename}"


# ---------------------------------------------------------------------------
# Plan persistence (token stash for the SSE apply GET)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r'^[0-9a-f]{32}$')


def staging_root():
    root = os.path.join(storage.system_path(), '.smart_import')
    os.makedirs(root, exist_ok=True)
    return root


def upload_root():
    root = os.path.join(storage.system_path(), '.smart_import_uploads')
    os.makedirs(root, exist_ok=True)
    return root


def upload_dir(token):
    """Absolute path of one uploaded-folder staging dir; validates the token."""
    if not _TOKEN_RE.match(token or ''):
        raise ValueError('invalid upload token')
    return os.path.join(upload_root(), token)


def create_upload():
    """Create a fresh uploaded-folder staging dir and return its token."""
    token = uuid.uuid4().hex
    os.makedirs(os.path.join(upload_root(), token), exist_ok=True)
    return token


def discard_upload(token):
    try:
        shutil.rmtree(upload_dir(token))
    except (OSError, ValueError):
        pass


def _plan_path(token):
    if not _TOKEN_RE.match(token or ''):
        raise ValueError('invalid plan token')
    return os.path.join(staging_root(), f'{token}.json')


def save_plan(plan):
    """Persist an apply plan and return its token."""
    token = uuid.uuid4().hex
    with open(_plan_path(token), 'w', encoding='utf-8') as f:
        json.dump(plan, f)
    return token


def load_plan(token):
    with open(_plan_path(token), 'r', encoding='utf-8') as f:
        return json.load(f)


def discard_plan(token):
    try:
        os.remove(_plan_path(token))
    except OSError:
        pass


def build_plan(base_dir, root_id, items, profile):
    """Turn reviewed proposals (possibly edited client-side) into a fully
    resolved, validated apply plan. ``base_dir`` is the resolved root path;
    ``items`` is the list of accepted proposal dicts.

    Each job stores an absolute, re-validated source path plus the canonical
    target so the SSE apply needs no further client input. Invalid items are
    dropped with a recorded reason (returned in ``skipped``)."""
    profile = normalize_profile(profile)
    root = os.path.abspath(base_dir)
    jobs = []
    skipped = []

    for it in items or []:
        rel = str(it.get('src_rel', '')).strip().lstrip('/')
        full = storage.safe_join(root, rel) if rel else None
        if not full or not os.path.isfile(full):
            skipped.append({'src_rel': rel, 'reason': 'source file not found'})
            continue

        ext = str(it.get('ext') or '').lower()
        if not ext and '.' in rel:
            ext = rel.rsplit('.', 1)[-1].lower()
        file_format = _format_from_ext(ext)
        if not file_format:
            skipped.append({'src_rel': rel, 'reason': 'unsupported file type'})
            continue

        subject = str(it.get('subject', '') or '').strip().upper()
        source = str(it.get('source', '') or '').strip().upper()
        version = str(it.get('version', '') or '').strip().upper()
        asset_type = str(it.get('asset_type', '') or '').strip().upper()
        detail = str(it.get('detail', '') or '').strip()
        qid = str(it.get('qid', '') or '').strip()

        if source not in ALL_SOURCES or version not in VERSIONS or asset_type not in ASSET_TYPES:
            skipped.append({'src_rel': rel, 'reason': 'invalid slot (source/version/type)'})
            continue
        if not qid or not subject:
            skipped.append({'src_rel': rel, 'reason': 'missing QID / subject'})
            continue

        year = it.get('year')
        paper = it.get('paper')
        try:
            year = int(year) if year not in (None, '') else None
        except (TypeError, ValueError):
            year = None
        if source in PP_SOURCES and (year is None or not paper):
            skipped.append({'src_rel': rel, 'reason': 'missing year/paper for past-paper QID'})
            continue

        jobs.append({
            'src_abs': full,
            'src_rel': rel,
            'filename': it.get('filename') or os.path.basename(rel),
            'ext': ext,
            'format': file_format,
            'qid': qid,
            'subject': subject,
            'source': source,
            'year': year,
            'paper': (str(paper).upper() if paper else None),
            'detail': detail,
            'qno': it.get('qno'),
            'version': version,
            'asset_type': asset_type,
        })

    plan = {
        'root_id': root_id,
        'base_dir': root,
        'overwrite': bool(profile['overwrite']),
        'backup': bool(profile['backup']),
        'create_missing': bool(profile['create_missing']),
        'jobs': jobs,
        'skipped': skipped,
    }
    return plan


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _backup_files(file_paths, source_path, batch_dir):
    """Copy existing slot files into the backup dir (preserving rel layout)."""
    for rel in file_paths:
        src = os.path.join(source_path, *rel.split('/'))
        if not os.path.isfile(src):
            continue
        dest = os.path.join(batch_dir, *rel.split('/'))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            shutil.copy2(src, dest)
        except OSError as e:
            logger.warning('Smart Import backup failed for %s: %s', rel, e)


def _ensure_question(job):
    """Fetch the question for a job, creating it when create-missing is on.
    Returns ``(question, created)`` or ``(None, False)`` if it cannot exist."""
    q = Question.query.filter_by(qid=job['qid']).first()
    if q:
        return q, False

    # Create-missing path: build a question from the resolved components.
    q = Question(qid=job['qid'])
    q.subject = job['subject']
    q.source = job['source']
    if job['source'] in PP_SOURCES:
        q.year = job['year']
        q.paper = job['paper']
    else:
        q.year = None
        q.paper = None
    try:
        q.qno = int(job['qno']) if job.get('qno') not in (None, '') else parse_qno_from_qid(job['qid'])
    except (TypeError, ValueError):
        q.qno = parse_qno_from_qid(job['qid'])
    q.q_type = determine_question_type(job['subject'], job['source'], job.get('paper'))
    q.level = None
    q.section = None
    db.session.add(q)
    db.session.commit()
    return q, True


def parse_qno_from_qid(qid):
    """Last-resort qno extraction from a QID's trailing ``_Q<n>``."""
    m = re.search(r'_Q(\d+)$', qid or '')
    return int(m.group(1)) if m else 0


def _apply_img(question, job, source_path, overwrite, backup, batch_dir):
    """Overwrite the whole IMG slot with the single source image."""
    from PIL import Image
    from app import batch_image_gen

    existing = QuestionAsset.query.filter_by(
        question_id=question.id, asset_type=job['asset_type'],
        version=job['version'], file_format='IMG').all()
    if existing and not overwrite:
        return 'skip', 'IMG slot occupied and overwrite is off'

    if backup and existing:
        _backup_files([a.file_path for a in existing], source_path, batch_dir)

    with Image.open(job['src_abs']) as im:
        im.load()
        pages = [im.copy()]
    summary = batch_image_gen.replace_img_assets(
        question, job['asset_type'], job['version'], pages, stitch=False,
        source_path=source_path)
    verb = 'Replaced' if summary['deleted'] else 'Added'
    return 'ok', f"{verb} IMG ({summary['deleted']} old -> {summary['wrote']} new part)"


def _apply_doc(question, job, source_path, overwrite, backup, batch_dir):
    """Single-slot DOC replace."""
    from app import doc_thumbnails

    existing = QuestionAsset.query.filter_by(
        question_id=question.id, asset_type=job['asset_type'],
        version=job['version'], file_format='DOC').all()
    if existing and not overwrite:
        return 'skip', 'DOC slot occupied and overwrite is off'

    if backup and existing:
        _backup_files([a.file_path for a in existing], source_path, batch_dir)

    # Remove old DOC rows + files + cached thumbnails.
    deleted_ids = []
    for a in existing:
        old_abs = os.path.join(source_path, *a.file_path.split('/'))
        try:
            if os.path.isfile(old_abs):
                os.remove(old_abs)
        except OSError as e:
            logger.warning('Could not remove old DOC %s: %s', old_abs, e)
        deleted_ids.append(a.id)
        db.session.delete(a)
    db.session.flush()

    ext = job['ext'] if job['ext'] in ('doc', 'docx') else 'docx'
    rel = _canonical_rel(job['subject'], job['source'], job['year'], job['paper'],
                         job['detail'], job['qid'], job['version'], job['asset_type'], ext)
    dest = os.path.join(source_path, *rel.split('/'))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(job['src_abs'], dest)

    asset = QuestionAsset(
        question_id=question.id, asset_type=job['asset_type'], file_format='DOC',
        version=job['version'], file_path=rel, part_number=1)
    db.session.add(asset)
    db.session.commit()

    for aid in deleted_ids:
        try:
            doc_thumbnails.on_doc_asset_deleted(aid)
        except Exception as e:
            logger.warning('DOC thumbnail cleanup skipped: %s', e)
    try:
        doc_thumbnails.on_doc_asset_created(asset)
    except Exception as e:
        logger.warning('DOC thumbnail schedule skipped: %s', e)

    verb = 'Replaced' if existing else 'Added'
    return 'ok', f'{verb} DOC'


def _apply_md(question, job, source_path, overwrite, backup, batch_dir):
    """Single-slot MD replace."""
    md_max = current_app.config.get('MD_MAX_SIZE_BYTES', 5 * 1024 * 1024)
    try:
        size = os.path.getsize(job['src_abs'])
    except OSError:
        size = 0
    if size > md_max:
        return 'skip', f'MD exceeds MD_MAX_SIZE_BYTES ({md_max} bytes)'

    existing = QuestionAsset.query.filter_by(
        question_id=question.id, asset_type=job['asset_type'],
        version=job['version'], file_format='MD').first()
    if existing and not overwrite:
        return 'skip', 'MD slot occupied and overwrite is off'

    if backup and existing:
        _backup_files([existing.file_path], source_path, batch_dir)

    rel = _canonical_rel(job['subject'], job['source'], job['year'], job['paper'],
                         job['detail'], job['qid'], job['version'], job['asset_type'], 'md')
    dest = os.path.join(source_path, *rel.split('/'))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(job['src_abs'], dest)

    verb = 'Added'
    if existing:
        verb = 'Replaced'
        # If the old file path differs from the canonical name, remove it.
        old_abs = os.path.join(source_path, *existing.file_path.split('/'))
        if os.path.normcase(old_abs) != os.path.normcase(dest) and os.path.isfile(old_abs):
            try:
                os.remove(old_abs)
            except OSError:
                pass
        existing.file_path = rel
        asset_id = existing.id
    else:
        asset = QuestionAsset(
            question_id=question.id, asset_type=job['asset_type'], file_format='MD',
            version=job['version'], file_path=rel, part_number=1)
        db.session.add(asset)
        db.session.flush()
        asset_id = asset.id
    db.session.commit()

    try:
        md_render.invalidate(asset_id)
    except Exception as e:
        logger.warning('MD cache invalidate skipped: %s', e)

    return 'ok', f'{verb} MD'


def iter_apply(plan, app):
    """Generator yielding progress events for an apply plan. Mirrors the
    ingestion SSE event shape: ``{type, message, current, total, stats}``.

    Runs inside a pushed app context provided by the caller's generator (the
    route wraps each yielded dict as an SSE ``data:`` frame)."""
    jobs = plan.get('jobs', [])
    total = len(jobs)
    overwrite = bool(plan.get('overwrite', True))
    backup = bool(plan.get('backup', False))
    create_missing = bool(plan.get('create_missing', False))
    source_path = current_app.config['SOURCE_PATH']

    batch_dir = None
    if backup:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        batch_dir = os.path.join(storage.system_path(), 'ImportBackups', ts)
        os.makedirs(batch_dir, exist_ok=True)

    stats = {'ok': 0, 'created_questions': 0, 'skipped': 0, 'errors': 0}
    yield {'type': 'info', 'message': f'Applying {total} file(s)...', 'total': total}
    if backup:
        yield {'type': 'info', 'message': f'Backing up replaced files to {batch_dir}'}

    current = 0
    for job in jobs:
        current += 1
        label = f"{job['qid']} [{job['asset_type']}/{job['version']}/{job['format']}]"
        try:
            question = Question.query.filter_by(qid=job['qid']).first()
            if question is None:
                if not create_missing:
                    stats['skipped'] += 1
                    yield {'type': 'skip', 'message': f'Skipped (no such question): {label}',
                           'current': current, 'total': total}
                    continue
                question, created = _ensure_question(job)
                if created:
                    stats['created_questions'] += 1
                    yield {'type': 'info', 'message': f'Created question {job["qid"]}',
                           'current': current, 'total': total}

            if job['format'] == 'IMG':
                state, msg = _apply_img(question, job, source_path, overwrite, backup, batch_dir)
            elif job['format'] == 'DOC':
                state, msg = _apply_doc(question, job, source_path, overwrite, backup, batch_dir)
            else:
                state, msg = _apply_md(question, job, source_path, overwrite, backup, batch_dir)

            if state == 'ok':
                stats['ok'] += 1
                yield {'type': 'success', 'message': f'{msg}: {label}',
                       'current': current, 'total': total}
            else:
                stats['skipped'] += 1
                yield {'type': 'skip', 'message': f'Skipped ({msg}): {label}',
                       'current': current, 'total': total}
        except Exception as e:
            db.session.rollback()
            stats['errors'] += 1
            logger.exception('Smart Import apply failed for %s', job.get('src_rel'))
            yield {'type': 'error', 'message': f'Error on {label}: {e}',
                   'current': current, 'total': total}

    yield {
        'type': 'done',
        'message': (f'Import complete. Applied: {stats["ok"]}, '
                    f'Created questions: {stats["created_questions"]}, '
                    f'Skipped: {stats["skipped"]}, Errors: {stats["errors"]}.'),
        'current': total, 'total': total,
        'stats': stats,
    }
