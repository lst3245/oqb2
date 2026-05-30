"""
AI Tools service: image proofreading + Markdown generation.

The two ``iter_*`` generators yield plain event dicts (``{type, message,
current?, total?, ...}``); the SSE routes in ``app/admin.py`` serialise
them as ``data: {json}\\n\\n``. Each generator takes a ``threading.Event``
``cancel`` flag that is checked between questions so a run can be stopped
server-side (to stop burning LLM tokens), unlike the legacy batch ops
that only disconnect the client.

All LLM access goes through ``app/llm_client.py`` (OpenAI-compatible).
"""
import json
import logging
import os
import threading
import uuid
from datetime import datetime

from app import db, md_render
from app.models import Question, QuestionAsset
from app import ai_prompts
from app import llm_client

logger = logging.getLogger(__name__)


# ==================== Cancellation registry ====================
#
# Single-process assumption (same as the settings hot-reload): the cancel
# flag lives in memory, so a multi-worker deployment would need the cancel
# POST to reach the same worker running the stream.

_AI_CANCEL: 'dict[str, threading.Event]' = {}
_AI_LOCK = threading.Lock()


def new_job():
    """Register a new cancellable job; returns ``(job_id, cancel_event)``."""
    job_id = uuid.uuid4().hex
    ev = threading.Event()
    with _AI_LOCK:
        _AI_CANCEL[job_id] = ev
    return job_id, ev


def cancel_job(job_id: str) -> bool:
    """Signal a running job to stop. Returns True if the job was known."""
    with _AI_LOCK:
        ev = _AI_CANCEL.get(job_id)
    if ev is not None:
        ev.set()
        return True
    return False


def finish_job(job_id: str) -> None:
    """Drop a finished job's cancel flag from the registry."""
    with _AI_LOCK:
        _AI_CANCEL.pop(job_id, None)


# ==================== Slot / path helpers ====================

def _slot_img_parts(question_id, asset_type, version):
    """All IMG parts for a (question, asset_type, version) slot, ordered."""
    return (QuestionAsset.query
            .filter_by(question_id=question_id, asset_type=asset_type,
                       version=version, file_format='IMG')
            .order_by(QuestionAsset.part_number)
            .all())


def _qb_detail(qid):
    """Mirror of admin._extract_qb_detail without the import cycle."""
    parts = qid.split('_')
    return parts[2] if len(parts) >= 4 else 'UNKNOWN'


def _md_rel_path(question, version, asset_type):
    """Canonical relative path for a generated MD asset (mirrors
    admin.create_md_asset)."""
    filename = f"{question.qid}_{version}_{asset_type}.md"
    if question.source in ('DSE', 'CE', 'AL'):
        folder = '/'.join([question.subject, 'PP', question.source,
                           str(question.year), question.paper])
    else:
        folder = '/'.join([question.subject, 'QB', _qb_detail(question.qid)])
    return f"{folder}/{filename}"


def _abs(source_path, rel_path):
    return os.path.join(source_path, *rel_path.split('/'))


def _empty_reply_hint(info):
    """Turn an LLM response's finish_reason into an actionable hint for the
    common 'empty reply' failure modes."""
    info = info or {}
    fr = info.get('finish_reason')
    if fr == 'length':
        return (' — hit the output-token limit before producing text '
                '(raise "Max output tokens" on the endpoint, or the model is a '
                'reasoning model spending its budget on hidden thinking)')
    if fr == 'content_filter':
        return ' — blocked by the provider content filter'
    usage = info.get('usage') or {}
    ct = usage.get('completion_tokens')
    extra = f' (finish_reason={fr}, completion_tokens={ct})' if fr or ct is not None else ''
    return (' — the endpoint returned no text content. Verify the model is '
            'vision-capable and actually received the image' + extra)


# ==================== Image checking (proofreading) ====================

def iter_check(qs, typed_version, ref_version, asset_types, recheck,
               config, image_max_dim, source_path, cancel):
    """Proofread each typed slot against the official reference slot.

    Yields event dicts. Updates ``check_state`` / ``check_result`` /
    ``checked_at`` on the TYPED asset rows.
    """
    work = [(q, atype) for q in qs for atype in asset_types]
    total = len(work)
    yield {'type': 'info',
           'message': f'Checking {typed_version} against {ref_version} — '
                      f'{len(qs)} question(s), {total} slot(s).'}

    ok = issues = skipped = errors = 0
    current = 0
    for question, atype in work:
        if cancel.is_set():
            yield {'type': 'info', 'message': 'Cancelled by user.',
                   'current': current, 'total': total}
            break
        current += 1
        label = f'{question.qid} / {atype} / {typed_version} vs {ref_version}'

        typed_assets = _slot_img_parts(question.id, atype, typed_version)
        ref_assets = _slot_img_parts(question.id, atype, ref_version)
        if not typed_assets:
            skipped += 1
            yield {'type': 'skip', 'message': f'{label} — no {typed_version} IMG',
                   'current': current, 'total': total}
            continue
        if not ref_assets:
            skipped += 1
            yield {'type': 'skip', 'message': f'{label} — no {ref_version} reference IMG',
                   'current': current, 'total': total}
            continue
        if not recheck and any(a.check_state in ('ok', 'issues') for a in typed_assets):
            skipped += 1
            yield {'type': 'skip', 'message': f'{label} — already checked (recheck off)',
                   'current': current, 'total': total}
            continue

        # Prepare images: reference first, then typed.
        try:
            ref_imgs = [llm_client.prepare_image(_abs(source_path, a.file_path), image_max_dim)
                        for a in ref_assets]
            typed_imgs = [llm_client.prepare_image(_abs(source_path, a.file_path), image_max_dim)
                          for a in typed_assets]
        except Exception as e:
            errors += 1
            logger.exception('Image prep failed for %s', label)
            yield {'type': 'error', 'message': f'{label} — image load failed: {e}',
                   'current': current, 'total': total}
            continue

        user_text = (
            ai_prompts.build_check_user_text(typed_version, ref_version, atype)
            + f"\n\nImage order: the first {len(ref_imgs)} image(s) are the "
              f"OFFICIAL ({ref_version}) version; the remaining {len(typed_imgs)} "
              f"image(s) are the TYPED ({typed_version}) version to proofread."
        )
        try:
            text, info = llm_client.chat(config, ai_prompts.CHECK_SYSTEM,
                                         user_text, ref_imgs + typed_imgs)
        except llm_client.LLMError as e:
            errors += 1
            yield {'type': 'error', 'message': f'{label} — LLM error: {e}',
                   'current': current, 'total': total}
            continue

        if not (text or '').strip():
            errors += 1
            hint = _empty_reply_hint(info)
            logger.warning('Empty check reply for %s; finish_reason=%s raw=%s',
                           label, (info or {}).get('finish_reason'),
                           str((info or {}).get('raw'))[:1000])
            yield {'type': 'error', 'message': f'{label} — model returned an empty reply{hint}',
                   'current': current, 'total': total}
            continue

        parsed = ai_prompts.parse_check_result(text)
        now = datetime.utcnow()
        if parsed is None:
            # Got a reply we couldn't parse — record it as an error state.
            state = 'error'
            result = {'status': 'error', 'issues': [], 'raw': (text or '')[:4000],
                      'model': config.model_name, 'ref_version': ref_version,
                      'checked_by': 'ai'}
            errors += 1
            msg = f'{label} — unparseable model reply (stored raw)'
            ev_type = 'error'
        else:
            state = parsed['status']  # 'ok' or 'issues'
            result = {'status': state, 'issues': parsed['issues'],
                      'model': config.model_name, 'ref_version': ref_version,
                      'checked_by': 'ai'}
            if state == 'ok':
                ok += 1
                msg = f'{label} — OK (no issues)'
                ev_type = 'success'
            else:
                issues += 1
                n = len(parsed['issues'])
                first = parsed['issues'][0]['description'] if parsed['issues'] else ''
                msg = f'{label} — {n} issue(s): {first[:160]}'
                ev_type = 'success'

        try:
            encoded = json.dumps(result, ensure_ascii=False)
            for a in typed_assets:
                a.check_state = state
                a.check_result = encoded
                a.checked_at = now
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            errors += 1
            logger.exception('DB write failed for %s', label)
            yield {'type': 'error', 'message': f'{label} — DB write failed: {e}',
                   'current': current, 'total': total}
            continue

        yield {'type': ev_type, 'message': msg, 'state': state,
               'current': current, 'total': total}

    if not cancel.is_set():
        yield {
            'type': 'done',
            'message': f'Done. OK: {ok}, with issues: {issues}, '
                       f'skipped: {skipped}, errors: {errors}.',
            'current': total, 'total': total,
            'stats': {'ok': ok, 'issues': issues, 'skipped': skipped, 'errors': errors},
        }
    else:
        yield {'type': 'done', 'message': 'Stopped.', 'current': current, 'total': total,
               'stats': {'ok': ok, 'issues': issues, 'skipped': skipped, 'errors': errors}}


# ==================== Markdown generation ====================

def iter_generate_md(qs, source_version, target_version, asset_types, overwrite,
                     embed_image, config, image_max_dim, md_max_bytes,
                     source_path, cancel):
    """Transcribe each source IMG slot into a Markdown asset for the target
    slot. Yields event dicts; writes the .md file + upserts the asset row."""
    work = [(q, atype) for q in qs for atype in asset_types]
    total = len(work)
    yield {'type': 'info',
           'message': f'Generating Markdown ({source_version} image -> '
                      f'{target_version} MD) — {len(qs)} question(s), {total} slot(s).'}

    created = skipped = errors = 0
    current = 0
    for question, atype in work:
        if cancel.is_set():
            yield {'type': 'info', 'message': 'Cancelled by user.',
                   'current': current, 'total': total}
            break
        current += 1
        label = f'{question.qid} / {atype} / {source_version} -> {target_version} MD'

        src_assets = _slot_img_parts(question.id, atype, source_version)
        if not src_assets:
            skipped += 1
            yield {'type': 'skip', 'message': f'{label} — no {source_version} source IMG',
                   'current': current, 'total': total}
            continue

        existing = (QuestionAsset.query
                    .filter_by(question_id=question.id, asset_type=atype,
                               version=target_version, file_format='MD')
                    .first())
        if existing and not overwrite:
            skipped += 1
            yield {'type': 'skip', 'message': f'{label} — MD exists (overwrite off)',
                   'current': current, 'total': total}
            continue

        try:
            imgs = [llm_client.prepare_image(_abs(source_path, a.file_path), image_max_dim)
                    for a in src_assets]
        except Exception as e:
            errors += 1
            logger.exception('Image prep failed for %s', label)
            yield {'type': 'error', 'message': f'{label} — image load failed: {e}',
                   'current': current, 'total': total}
            continue

        user_text = ai_prompts.build_md_user_text(source_version, atype)
        try:
            text, info = llm_client.chat(config, ai_prompts.MD_SYSTEM, user_text, imgs)
        except llm_client.LLMError as e:
            errors += 1
            yield {'type': 'error', 'message': f'{label} — LLM error: {e}',
                   'current': current, 'total': total}
            continue

        md = ai_prompts.strip_md_fences(text)
        if not md.strip():
            errors += 1
            hint = _empty_reply_hint(info)
            logger.warning('Empty MD reply for %s; finish_reason=%s raw=%s',
                           label, (info or {}).get('finish_reason'),
                           str((info or {}).get('raw'))[:1000])
            yield {'type': 'error', 'message': f'{label} — model returned empty Markdown{hint}',
                   'current': current, 'total': total}
            continue

        # Embed original source image(s) as a figure fallback so diagrams
        # are never lost in transcription.
        if embed_image:
            parts = [md, '\n\n---\n', f'*Source image(s) — {source_version}:*\n']
            for i, a in enumerate(src_assets, 1):
                try:
                    uri = llm_client.read_image_data_uri(_abs(source_path, a.file_path))
                    parts.append(f'\n![{atype} {source_version} part {i}]({uri})\n')
                except Exception:
                    logger.warning('Could not embed source image for %s part %s', label, i)
            md = ''.join(parts)

        payload = md.encode('utf-8')
        if len(payload) > md_max_bytes:
            skipped += 1
            yield {'type': 'skip',
                   'message': f'{label} — generated MD {len(payload)} bytes exceeds limit '
                              f'{md_max_bytes} (try without embedding the image)',
                   'current': current, 'total': total}
            continue

        # Write file + upsert asset row.
        try:
            rel_path = _md_rel_path(question, target_version, atype)
            abs_path = _abs(source_path, rel_path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, 'wb') as f:
                f.write(payload)

            if existing:
                existing.file_path = rel_path
                asset = existing
            else:
                asset = QuestionAsset(
                    question_id=question.id, asset_type=atype,
                    file_format='MD', version=target_version,
                    file_path=rel_path, part_number=1,
                )
                db.session.add(asset)
            db.session.commit()
            md_render.invalidate(asset.id)
        except Exception as e:
            db.session.rollback()
            errors += 1
            logger.exception('MD write failed for %s', label)
            yield {'type': 'error', 'message': f'{label} — write failed: {e}',
                   'current': current, 'total': total}
            continue

        created += 1
        verb = 'updated' if existing else 'created'
        yield {'type': 'success',
               'message': f'{label} — {verb} MD ({len(payload)} bytes)',
               'current': current, 'total': total}

    if not cancel.is_set():
        yield {
            'type': 'done',
            'message': f'Done. Created/updated: {created}, skipped: {skipped}, errors: {errors}.',
            'current': total, 'total': total,
            'stats': {'created': created, 'skipped': skipped, 'errors': errors},
        }
    else:
        yield {'type': 'done', 'message': 'Stopped.', 'current': current, 'total': total,
               'stats': {'created': created, 'skipped': skipped, 'errors': errors}}
