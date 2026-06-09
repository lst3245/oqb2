"""
OpenAI-compatible LLM client for the AI Tools admin feature.

Configured endpoints (``app.models.LLMConfig``) use either Chat Completions
(``POST {base_url}/chat/completions``) or the Responses API
(``POST {base_url}/responses``), with optional reasoning controls for
providers such as OpenRouter and Poe. Base64 image input is supported on
both protocols so this single adapter serves local servers (Ollama, LM
Studio, vLLM, ...) and cloud providers (OpenAI, OpenRouter, Poe, ...).

API-key handling is hybrid (see ``resolve_api_key``): a per-endpoint key
entered in the admin UI is stored Fernet-encrypted in ``api_key_enc``;
when blank the client falls back to ``os.getenv(api_key_env or
'LLM_API_KEY')``. The plaintext key never leaves the server.
"""
import base64
import hashlib
import io
import json
import mimetypes
import os
from binascii import Error as binascii_error

import requests
from flask import current_app


class LLMError(Exception):
    """Raised for any failure talking to an LLM endpoint (HTTP, timeout,
    auth, or an unexpected response shape)."""


# ==================== API-key encryption (Fernet) ====================

def _fernet():
    """Build a Fernet cipher from a deterministic 32-byte key.

    The secret material is ``LLM_KEY_SECRET`` when set, else ``SECRET_KEY``.
    Either is hashed to exactly 32 bytes and urlsafe-b64 encoded so any
    human-readable secret string yields a valid Fernet key. Rotating the
    secret invalidates previously stored ciphertexts (documented).
    """
    from cryptography.fernet import Fernet
    secret = (current_app.config.get('LLM_KEY_SECRET')
              or current_app.config.get('SECRET_KEY') or 'oqb-llm')
    digest = hashlib.sha256(secret.encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_key(plaintext: str) -> str:
    """Encrypt a plaintext API key for storage in ``LLMConfig.api_key_enc``."""
    if not plaintext:
        return ''
    return _fernet().encrypt(plaintext.encode('utf-8')).decode('ascii')


def decrypt_key(ciphertext: str) -> str:
    """Decrypt a stored API key. Returns '' if empty or undecryptable
    (e.g. the encryption secret was rotated)."""
    if not ciphertext:
        return ''
    try:
        return _fernet().decrypt(ciphertext.encode('ascii')).decode('utf-8')
    except Exception:
        return ''


def resolve_api_key(config) -> str:
    """Resolve the effective API key for an endpoint: the per-endpoint
    encrypted key if present, otherwise the ``.env`` fallback named by
    ``api_key_env`` (default ``LLM_API_KEY``)."""
    key = decrypt_key(config.api_key_enc or '')
    if key:
        return key
    env_name = (config.api_key_env or 'LLM_API_KEY').strip() or 'LLM_API_KEY'
    if env_name == 'LLM_API_KEY':
        return current_app.config.get('LLM_API_KEY', '') or os.getenv('LLM_API_KEY', '')
    return os.getenv(env_name, '')


def resolve_default_endpoint(setting_key: str, vision_only: bool = True,
                             named_vision_only: bool | None = None):
    """Resolve a per-feature default LLM endpoint.

    1. If ``current_app.config[setting_key]`` names an enabled endpoint, use
       it (subject to ``named_vision_only`` — defaults to ``vision_only``).
    2. Otherwise auto-pick the first enabled endpoint ordered by
       ``sort_order, name`` (filtered to vision-capable when ``vision_only``).

    The two flags are separable because the dashboard Explain tutor allows
    naming a text-only endpoint (text-only questions still work) but auto-
    picks a vision endpoint when nothing is configured. Other features
    require vision in both branches and pass the default ``True / True``.

    Returns the ``LLMConfig`` row or ``None`` when nothing matches. Must be
    called inside an app context (typical Flask request).
    """
    from app.models import LLMConfig

    if named_vision_only is None:
        named_vision_only = vision_only

    preferred = (current_app.config.get(setting_key) or '').strip()
    if preferred:
        q = LLMConfig.query.filter_by(name=preferred, enabled=True)
        if named_vision_only:
            q = q.filter_by(supports_vision=True)
        cfg = q.first()
        if cfg:
            return cfg
    q = LLMConfig.query.filter_by(enabled=True)
    if vision_only:
        q = q.filter_by(supports_vision=True)
    return q.order_by(LLMConfig.sort_order, LLMConfig.name).first()


# ==================== Image preparation ====================

def prepare_image(abs_path: str, max_dim: int = 1600):
    """Load an image, downscale its long edge to ``max_dim``, and return
    ``(b64, mime)`` ready for an OpenAI image_url block. Re-encoded as JPEG
    to keep payloads small while staying legible for proofreading.

    Images with an alpha channel (RGBA / LA / palette-with-transparency —
    common for these exported question scans) are composited onto a WHITE
    background first. A naive ``convert('RGB')`` fills transparent pixels with
    black, which buries black text on a black background (the model then sees
    "a completely black image").
    """
    from PIL import Image
    with Image.open(abs_path) as im:
        im.load()
        has_alpha = im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info)
        if has_alpha:
            rgba = im.convert('RGBA')
            bg = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
            bg.alpha_composite(rgba)
            im = bg.convert('RGB')
        elif im.mode != 'RGB':
            im = im.convert('RGB')
        w, h = im.size
        longest = max(w, h)
        if max_dim and longest > max_dim:
            scale = max_dim / float(longest)
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        buf = io.BytesIO()
        im.save(buf, format='JPEG', quality=90)
    return base64.b64encode(buf.getvalue()).decode('ascii'), 'image/jpeg'


def sent_image_size(abs_path: str, max_dim: int):
    """Return ``(w, h)`` the vision model actually sees for ``abs_path`` after
    :func:`prepare_image`'s long-edge downscale to ``max_dim``. Used so boxes
    answered in raw pixels are normalised against the right dimensions.
    Returns ``(None, None)`` if the file cannot be read."""
    try:
        from PIL import Image
        with Image.open(abs_path) as im:
            w, h = im.size
    except Exception:
        return None, None
    longest = max(w, h)
    if max_dim and longest > max_dim:
        scale = max_dim / float(longest)
        return max(1, int(w * scale)), max(1, int(h * scale))
    return w, h


def read_image_data_uri(abs_path: str) -> str:
    """Read an image file verbatim and return a ``data:<mime>;base64,...``
    URI of the ORIGINAL bytes — used to embed source figures into generated
    Markdown so diagrams aren't lost."""
    mime = mimetypes.guess_type(abs_path)[0] or 'image/png'
    with open(abs_path, 'rb') as f:
        raw = f.read()
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def prepare_image_from_data_url(data_url: str, max_dim: int = 1600):
    """Decode a ``data:image/...;base64,...`` URL (e.g. from the Explain
    modal's user-attached images), downscale, and return ``(b64, 'image/jpeg')``
    ready for an OpenAI ``image_url`` block.

    Mirrors :func:`prepare_image` but takes the raw bytes from the URL
    instead of opening a file on disk. Re-encoded as JPEG (q=88) to keep
    the over-the-wire payload small — the original mime / colour profile
    aren't preserved, but for VLM input that's not a concern.

    Raises :class:`ValueError` for malformed input. The caller is expected
    to reject the request with a 400 in that case.
    """
    import re
    from PIL import Image, UnidentifiedImageError

    if not isinstance(data_url, str) or not data_url.startswith('data:'):
        raise ValueError('not a data URL')
    m = re.match(r'data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$', data_url, re.DOTALL)
    if not m:
        raise ValueError('not a base64 image data URL')
    try:
        raw = base64.b64decode(m.group(2), validate=False)
    except (ValueError, binascii_error):
        raise ValueError('invalid base64 in data URL')
    if not raw:
        raise ValueError('empty image bytes')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            has_alpha = im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info)
            if has_alpha:
                rgba = im.convert('RGBA')
                bg = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
                bg.alpha_composite(rgba)
                im = bg.convert('RGB')
            elif im.mode != 'RGB':
                im = im.convert('RGB')
            w, h = im.size
            longest = max(w, h)
            if max_dim and longest > max_dim:
                scale = max_dim / float(longest)
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            buf = io.BytesIO()
            im.save(buf, format='JPEG', quality=88)
    except UnidentifiedImageError:
        raise ValueError('image bytes could not be decoded')
    return base64.b64encode(buf.getvalue()).decode('ascii'), 'image/jpeg'


def prepare_image_from_pil(im, max_dim: int = 1600):
    """Downscale a PIL Image's long edge to ``max_dim`` and return
    ``(b64, 'image/jpeg')`` ready for an OpenAI ``image_url`` block.

    Mirrors :func:`prepare_image` but takes an in-memory PIL Image instead of
    a file on disk — used when MD / DOC source assets are rendered to pages on
    the fly (no IMG exists for the slot) and sent to the model for
    proofreading. Alpha is composited onto white (see :func:`prepare_image`).
    """
    from PIL import Image
    work = im
    has_alpha = work.mode in ('RGBA', 'LA') or (work.mode == 'P' and 'transparency' in work.info)
    if has_alpha:
        rgba = work.convert('RGBA')
        bg = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        work = bg.convert('RGB')
    elif work.mode != 'RGB':
        work = work.convert('RGB')
    w, h = work.size
    longest = max(w, h)
    if max_dim and longest > max_dim:
        scale = max_dim / float(longest)
        work = work.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    buf = io.BytesIO()
    work.save(buf, format='JPEG', quality=90)
    return base64.b64encode(buf.getvalue()).decode('ascii'), 'image/jpeg'


def _flatten_white(im):
    """Composite an alpha-bearing image onto white and return RGB (see
    prepare_image for why)."""
    from PIL import Image
    if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
        rgba = im.convert('RGBA')
        bg = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        return bg.convert('RGB')
    if im.mode != 'RGB':
        return im.convert('RGB')
    return im


def crop_image_data_uri(abs_path: str, box, pad: float = 0.02,
                        max_dim: int = 1400) -> str:
    """Crop ``abs_path`` to the fractional ``box`` ``[x1,y1,x2,y2]`` (0..1,
    top-left origin), pad slightly, downscale, and return a PNG data URI.

    Raises ``ValueError`` for a degenerate box so the caller can fall back to
    embedding the whole image.
    """
    from PIL import Image
    x1, y1, x2, y2 = box
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    x1 = max(0.0, x1 - pad); y1 = max(0.0, y1 - pad)
    x2 = min(1.0, x2 + pad); y2 = min(1.0, y2 + pad)
    with Image.open(abs_path) as im:
        im.load()
        im = _flatten_white(im)
        w, h = im.size
        left, top = int(x1 * w), int(y1 * h)
        right, bottom = int(x2 * w), int(y2 * h)
        if right - left < 4 or bottom - top < 4:
            raise ValueError('degenerate crop box')
        crop = im.crop((left, top, right, bottom))
        cw, ch = crop.size
        longest = max(cw, ch)
        if max_dim and longest > max_dim:
            scale = max_dim / float(longest)
            crop = crop.resize((max(1, int(cw * scale)), max(1, int(ch * scale))))
        buf = io.BytesIO()
        crop.save(buf, format='PNG')
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"


# ==================== LLM transport (Chat Completions + Responses) ==========

_REQUEST_EXTRA_MAX_BYTES = 8192


def _image_block(b64, mime):
    """An OpenAI image_url content block from a ``(b64, mime)`` pair."""
    return {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}'}}


def _api_protocol(config) -> str:
    """Return ``'chat'`` or ``'responses'`` for the configured endpoint."""
    p = (getattr(config, 'api_protocol', '') or 'chat').strip().lower()
    return 'responses' if p == 'responses' else 'chat'


def parse_request_extra_json(raw: str) -> dict:
    """Validate and normalise endpoint ``request_extra_json`` for storage.

    Must be a JSON object. Raises ``ValueError`` on invalid input.
    """
    text = (raw or '').strip()
    if not text:
        return {}
    if len(text.encode('utf-8')) > _REQUEST_EXTRA_MAX_BYTES:
        raise ValueError(f'must be at most {_REQUEST_EXTRA_MAX_BYTES} bytes')
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise ValueError(f'invalid JSON: {e}') from e
    if not isinstance(obj, dict):
        raise ValueError('must be a JSON object')
    return obj


def _resolve_reasoning(config):
    """Resolve effective reasoning settings for an endpoint.

    Returns ``None`` when reasoning should be omitted, else a dict suitable
    for the provider ``reasoning`` request param.
    """
    effort = (getattr(config, 'reasoning_effort', '') or '').strip().lower()
    if not effort:
        effort = (current_app.config.get('LLM_REASONING_EFFORT_DEFAULT')
                  or 'off').strip().lower()
    if not effort or effort == 'off':
        return None

    summary = (getattr(config, 'reasoning_summary', '') or '').strip().lower()
    if not summary:
        summary = (current_app.config.get('LLM_REASONING_SUMMARY_DEFAULT')
                   or 'auto').strip().lower()

    out = {'effort': effort}
    if summary in ('auto', 'none'):
        out['summary'] = summary

    max_rt = getattr(config, 'reasoning_max_tokens', None)
    try:
        if max_rt is not None and int(max_rt) > 0:
            out['max_tokens'] = int(max_rt)
    except (TypeError, ValueError):
        pass
    return out


def _is_claude_model(config) -> bool:
    """True when the configured model is a Claude / Anthropic bot."""
    model = (getattr(config, 'model_name', '') or '').strip().lower()
    if 'claude' in model:
        return True
    provider = (getattr(config, 'provider', '') or '').strip().lower()
    return provider in ('anthropic', 'claude')


def _apply_reasoning_params(payload: dict, config, reasoning: dict | None) -> None:
    """Attach provider-appropriate reasoning / thinking controls to *payload*.

    Claude 4.6+ (incl. Opus 4.7 via Poe Responses) rejects the legacy
    ``thinking.type.enabled`` mapping that gateways derive from OpenAI-style
    ``reasoning.effort``. Those models require adaptive thinking plus
    ``output_config.effort`` instead.
    """
    if not reasoning:
        return

    if _is_claude_model(config):
        effort = reasoning.get('effort') or 'high'
        summary = reasoning.get('summary') or 'auto'
        thinking: dict = {'type': 'adaptive'}
        if summary == 'auto':
            thinking['display'] = 'summarized'
        payload['thinking'] = thinking
        payload['output_config'] = {'effort': effort}
        return

    payload['reasoning'] = reasoning


def _merge_request_extra(payload: dict, config) -> dict:
    """Shallow-merge validated ``request_extra_json`` into a request body."""
    raw = getattr(config, 'request_extra_json', None) or ''
    if not (raw or '').strip():
        return payload
    try:
        extra = json.loads(raw)
    except ValueError as e:
        raise LLMError(f'invalid request_extra_json ({e})')
    if not isinstance(extra, dict):
        raise LLMError('request_extra_json must be a JSON object')
    merged = dict(payload)
    merged.update(extra)
    return merged


def _request_headers(config, *, stream: bool = False) -> dict:
    headers = {'Content-Type': 'application/json'}
    if stream:
        headers['Accept'] = 'text/event-stream'
    api_key = resolve_api_key(config)
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    return headers


def _content_to_plain_text(content) -> str:
    """Extract plain text from a Chat Completions ``content`` value."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for blk in content:
            if isinstance(blk, dict) and blk.get('type') == 'text':
                t = blk.get('text') or ''
                if t:
                    parts.append(t)
        return '\n'.join(parts)
    return ''


def _messages_to_responses_input(messages):
    """Convert OpenAI ``messages`` into Responses ``instructions`` + ``input``.

    Poe / OpenAI Responses accept:
    - ``input`` as a plain string for a single text-only user turn
    - ``input`` as ``[{role, content: str}, ...]`` for multi-turn text
    - typed ``input_text`` / ``input_image`` blocks only inside
      ``type: message`` items for multimodal **user** turns
    - assistant replay uses plain-string ``content`` (or ``output_text``
      blocks when replaying structured output) — never ``input_text``
    """
    instructions_parts: list[str] = []
    input_items: list[dict] = []

    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = (msg.get('role') or 'user').strip().lower()
        content = msg.get('content', '')

        if role == 'system':
            text = _content_to_plain_text(content).strip()
            if text:
                instructions_parts.append(text)
            continue

        if role == 'assistant':
            text = _content_to_plain_text(content).strip()
            if text:
                input_items.append({'role': 'assistant', 'content': text})
            continue

        if role not in ('user', 'developer'):
            role = 'user'

        if isinstance(content, str):
            if content.strip():
                input_items.append({'role': role, 'content': content})
            continue

        if isinstance(content, list):
            text_parts: list[str] = []
            image_urls: list[str] = []
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                btype = blk.get('type')
                if btype == 'text':
                    t = blk.get('text') or ''
                    if t:
                        text_parts.append(t)
                elif btype == 'image_url':
                    url = (blk.get('image_url') or {}).get('url', '')
                    if url:
                        image_urls.append(url)

            if image_urls:
                blocks: list[dict] = []
                joined = '\n'.join(text_parts).strip()
                if joined:
                    blocks.append({'type': 'input_text', 'text': joined})
                for url in image_urls:
                    blocks.append({'type': 'input_image', 'image_url': url})
                input_items.append({
                    'type': 'message',
                    'role': role,
                    'content': blocks,
                })
            else:
                joined = '\n'.join(text_parts).strip()
                if joined:
                    input_items.append({'role': role, 'content': joined})

    instructions = '\n\n'.join(instructions_parts).strip()
    return instructions, input_items


def _responses_input_value(instructions: str, input_items: list) -> str | list:
    """Pick the simplest valid ``input`` shape for the Responses API.

    A lone text-only user turn is sent as a plain string (Poe/OpenAI basic
    usage); everything else stays as an item list.
    """
    if not input_items:
        return ''
    if len(input_items) == 1:
        item = input_items[0]
        if (isinstance(item, dict)
                and item.get('role') == 'user'
                and isinstance(item.get('content'), str)
                and item.get('type') != 'message'):
            return item['content']
    return input_items


def _resolve_service_tier(config):
    """Pick the service tier to send. Batch SSE ops set a transient
    ``config._batch = True`` attribute (NOT a mapped column, so it's never
    written to the DB) to opt into ``service_tier_batch``; everything else
    (single / interactive calls) uses ``service_tier``. Blank ⇒ omit the param.
    """
    if getattr(config, '_batch', False):
        tier = getattr(config, 'service_tier_batch', '') or ''
    else:
        tier = getattr(config, 'service_tier', '') or ''
    return tier.strip()


def _build_chat_payload(config, messages, max_tokens=None, temperature=None,
                        *, stream: bool = False):
    """Build a Chat Completions request body."""
    payload = {
        'model': config.model_name,
        'messages': messages,
        'max_tokens': int(max_tokens or config.max_output_tokens or 4096),
        'temperature': float(config.temperature if temperature is None else temperature),
    }
    tier = _resolve_service_tier(config)
    if tier:
        payload['service_tier'] = tier
    _apply_reasoning_params(payload, config, _resolve_reasoning(config))
    if stream:
        payload['stream'] = True
    return _merge_request_extra(payload, config)


def _build_responses_payload(config, messages, max_tokens=None, temperature=None,
                             *, stream: bool = False):
    """Build a Responses API request body."""
    instructions, input_items = _messages_to_responses_input(messages)
    payload = {
        'model': config.model_name,
        'input': _responses_input_value(instructions, input_items),
        'max_output_tokens': int(max_tokens or config.max_output_tokens or 4096),
        'temperature': float(config.temperature if temperature is None else temperature),
    }
    if instructions:
        payload['instructions'] = instructions
    _apply_reasoning_params(payload, config, _resolve_reasoning(config))
    if stream:
        payload['stream'] = True
    return _merge_request_extra(payload, config)


def _api_url(config, protocol: str) -> str:
    base = config.base_url.rstrip('/')
    if protocol == 'responses':
        return base + '/responses'
    return base + '/chat/completions'


def _raise_api_error(data):
    if not isinstance(data, dict) or not data.get('error'):
        return
    err = data['error']
    msg = err.get('message') if isinstance(err, dict) else str(err)
    raise LLMError(f'API error: {msg}')


def _extract_block_text(blocks) -> str:
    parts: list[str] = []
    for blk in blocks or []:
        if isinstance(blk, dict):
            t = blk.get('text')
            if isinstance(t, str) and t:
                parts.append(t)
        elif isinstance(blk, str) and blk:
            parts.append(blk)
    return '\n'.join(parts)


def _extract_message_parts(data):
    """Pull assistant text and reasoning from a Chat Completions response.

    Returns ``(text, reasoning, finish_reason)``.
    """
    if not isinstance(data, dict):
        raise LLMError(f'unexpected response type: {type(data).__name__}')
    choices = data.get('choices') or []
    if not choices:
        raise LLMError(f'response has no choices: {str(data)[:300]}')
    choice = choices[0] or {}
    finish = choice.get('finish_reason')
    msg = choice.get('message') or {}
    content = msg.get('content')

    text = ''
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = _extract_block_text(content)

    reasoning = ''
    for k in ('reasoning_content', 'reasoning'):
        v = msg.get(k)
        if isinstance(v, str) and v.strip():
            reasoning = v
            break

    if not (text or '').strip():
        for k in ('reasoning_content', 'reasoning'):
            v = msg.get(k)
            if isinstance(v, str) and v.strip():
                text = v
                break

    if not (text or '').strip() and isinstance(choice.get('text'), str):
        text = choice['text']

    details = data.get('reasoning_details')
    if not reasoning and isinstance(details, list):
        reasoning = _extract_reasoning_details(details)

    return text or '', reasoning or '', finish


def _extract_reasoning_details(details) -> str:
    parts: list[str] = []
    for item in details or []:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get('text'), str):
            parts.append(item['text'])
        elif isinstance(item.get('summary'), str):
            parts.append(item['summary'])
        content = item.get('content')
        if isinstance(content, list):
            parts.append(_extract_block_text(content))
    return '\n'.join(p for p in parts if p)


def _extract_responses_parts(data):
    """Pull text and reasoning from a Responses API body.

    Returns ``(text, reasoning, finish_reason)``.
    """
    if not isinstance(data, dict):
        raise LLMError(f'unexpected response type: {type(data).__name__}')

    text = data.get('output_text') or ''
    reasoning_parts: list[str] = []
    text_parts: list[str] = []

    for item in data.get('output') or []:
        if not isinstance(item, dict):
            continue
        itype = (item.get('type') or '').lower()
        if itype in ('reasoning', 'reasoning_summary', 'summary_text'):
            content = item.get('content') or item.get('summary') or item.get('text')
            if isinstance(content, str) and content:
                reasoning_parts.append(content)
            elif isinstance(content, list):
                reasoning_parts.append(_extract_block_text(content))
        elif itype == 'message':
            for blk in item.get('content') or []:
                if not isinstance(blk, dict):
                    continue
                btype = (blk.get('type') or '').lower()
                if btype in ('output_text', 'text'):
                    t = blk.get('text') or ''
                    if t:
                        text_parts.append(t)

    if not text:
        text = '\n'.join(text_parts)

    reasoning = '\n'.join(reasoning_parts)
    details = data.get('reasoning_details')
    if not reasoning and isinstance(details, list):
        reasoning = _extract_reasoning_details(details)

    finish = data.get('status') or data.get('finish_reason')
    return text or '', reasoning or '', finish


def _extract_message_text(data):
    """Backward-compatible wrapper — returns ``(text, finish_reason)`` only."""
    text, _reasoning, finish = _extract_message_parts(data)
    return text, finish


def _post_messages(config, messages, max_tokens=None, temperature=None, timeout=None):
    """POST ``messages`` using the endpoint's configured API protocol."""
    if _api_protocol(config) == 'responses':
        return _post_responses(config, messages, max_tokens=max_tokens,
                               temperature=temperature, timeout=timeout)
    return _post_chat_completions(config, messages, max_tokens=max_tokens,
                                  temperature=temperature, timeout=timeout)


def _post_chat_completions(config, messages, max_tokens=None, temperature=None,
                           timeout=None):
    payload = _build_chat_payload(config, messages, max_tokens, temperature)
    headers = _request_headers(config)
    effective_timeout = int(timeout or config.timeout_seconds or 120)
    url = _api_url(config, 'chat')
    try:
        resp = requests.post(url, json=payload, headers=headers,
                             timeout=effective_timeout)
    except requests.Timeout:
        raise LLMError(f'request timed out after {effective_timeout}s')
    except requests.RequestException as e:
        raise LLMError(f'request failed: {e}')

    if resp.status_code != 200:
        raise LLMError(f'HTTP {resp.status_code}: {resp.text[:500]}')

    try:
        data = resp.json()
    except ValueError as e:
        raise LLMError(f'non-JSON response ({e}): {resp.text[:300]}')

    if isinstance(data, dict) and data.get('error') and not data.get('choices'):
        _raise_api_error(data)

    text, reasoning, finish = _extract_message_parts(data)
    info = {
        'usage': data.get('usage') or {},
        'finish_reason': finish,
        'reasoning': reasoning,
        'reasoning_details': data.get('reasoning_details'),
        'raw': data,
    }
    return text, info


def _post_responses(config, messages, max_tokens=None, temperature=None, timeout=None):
    payload = _build_responses_payload(config, messages, max_tokens, temperature)
    headers = _request_headers(config)
    effective_timeout = int(timeout or config.timeout_seconds or 120)
    url = _api_url(config, 'responses')
    try:
        resp = requests.post(url, json=payload, headers=headers,
                             timeout=effective_timeout)
    except requests.Timeout:
        raise LLMError(f'request timed out after {effective_timeout}s')
    except requests.RequestException as e:
        raise LLMError(f'request failed: {e}')

    if resp.status_code != 200:
        raise LLMError(f'HTTP {resp.status_code}: {resp.text[:500]}')

    try:
        data = resp.json()
    except ValueError as e:
        raise LLMError(f'non-JSON response ({e}): {resp.text[:300]}')

    if isinstance(data, dict) and data.get('error') and not data.get('output'):
        _raise_api_error(data)

    text, reasoning, finish = _extract_responses_parts(data)
    info = {
        'usage': data.get('usage') or {},
        'finish_reason': finish,
        'reasoning': reasoning,
        'reasoning_details': data.get('reasoning_details'),
        'raw': data,
    }
    return text, info


def chat(config, system: str, user_text: str, images=None):
    """Call ``{base_url}/chat/completions`` with a single user turn and return
    ``(text, info)``.

    ``images`` is a list of ``(b64, mime)`` tuples (from ``prepare_image``)
    appended as image_url content blocks after the user text. Raises
    ``LLMError`` on any failure.
    """
    images = images or []
    content = [{'type': 'text', 'text': user_text}]
    for b64, mime in images:
        content.append(_image_block(b64, mime))

    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': content})
    return _post_messages(config, messages)


def chat_messages(config, messages, max_tokens=None, temperature=None, timeout=None):
    """Call the configured API protocol with a pre-built OpenAI ``messages``
    array and return ``(text, info)``. Used by batch AI Tools and interactive
    chat. ``timeout`` overrides the endpoint default when set."""
    return _post_messages(config, messages, max_tokens=max_tokens,
                          temperature=temperature, timeout=timeout)


def _stream_chat_completions(config, messages, max_tokens=None, temperature=None,
                             timeout=None):
    """Stream Chat Completions SSE and yield OQB delta/done events."""
    payload = _build_chat_payload(config, messages, max_tokens, temperature,
                                  stream=True)
    headers = _request_headers(config, stream=True)
    effective_timeout = int(timeout or config.timeout_seconds or 120)
    url = _api_url(config, 'chat')

    accumulated_content: list[str] = []
    accumulated_reasoning: list[str] = []
    finish_reason = None
    usage: dict = {}

    try:
        resp = requests.post(url, json=payload, headers=headers,
                             timeout=effective_timeout, stream=True)
    except requests.Timeout:
        raise LLMError(f'request timed out after {effective_timeout}s')
    except requests.RequestException as e:
        raise LLMError(f'request failed: {e}')

    with resp:
        if resp.status_code != 200:
            raise LLMError(f'HTTP {resp.status_code}: {resp.text[:500]}')
        resp.encoding = 'utf-8'

        content_type = (resp.headers.get('content-type') or '').lower()
        is_streaming = ('text/event-stream' in content_type
                        or 'application/x-ndjson' in content_type
                        or 'stream' in content_type)

        if not is_streaming:
            try:
                data = resp.json()
            except ValueError as e:
                raise LLMError(f'non-JSON response ({e}): {resp.text[:300]}')
            if isinstance(data, dict) and data.get('error') and not data.get('choices'):
                _raise_api_error(data)
            text, reasoning, finish = _extract_message_parts(data)
            yield {
                'type': 'done',
                'text': text or '',
                'reasoning': reasoning or '',
                'finish_reason': finish,
                'usage': data.get('usage') or {},
            }
            return

        for raw_line in resp.iter_lines(decode_unicode=True):
            if raw_line is None:
                continue
            line = raw_line.strip()
            if not line or line.startswith(':'):
                continue
            if not line.startswith('data:'):
                continue
            payload_str = line[len('data:'):].strip()
            if payload_str == '[DONE]':
                break
            try:
                chunk = json.loads(payload_str)
            except ValueError:
                continue

            if isinstance(chunk, dict) and chunk.get('error') and not chunk.get('choices'):
                _raise_api_error(chunk)

            if isinstance(chunk, dict) and chunk.get('usage'):
                usage = chunk['usage']

            choices = (chunk.get('choices') or []) if isinstance(chunk, dict) else []
            if not choices:
                continue
            choice = choices[0] or {}
            delta = choice.get('delta') or {}
            content_delta = delta.get('content') or ''
            reasoning_delta = (delta.get('reasoning_content')
                               or delta.get('reasoning')
                               or '')

            fr = choice.get('finish_reason')
            if fr:
                finish_reason = fr

            if content_delta or reasoning_delta:
                accumulated_content.append(content_delta)
                accumulated_reasoning.append(reasoning_delta)
                yield {
                    'type': 'delta',
                    'content': content_delta,
                    'reasoning': reasoning_delta,
                }

    yield {
        'type': 'done',
        'text': ''.join(accumulated_content),
        'reasoning': ''.join(accumulated_reasoning),
        'finish_reason': finish_reason,
        'usage': usage,
    }


def _responses_stream_deltas(chunk: dict):
    """Extract content/reasoning deltas from a Responses API stream chunk."""
    content_delta = ''
    reasoning_delta = ''

    if not isinstance(chunk, dict):
        return content_delta, reasoning_delta

    etype = (chunk.get('type') or '').lower()

    if etype in ('response.output_text.delta', 'response.text.delta'):
        content_delta = chunk.get('delta') or chunk.get('text') or ''
    elif etype in ('response.reasoning.delta', 'response.reasoning_text.delta',
                   'response.reasoning_summary_text.delta'):
        reasoning_delta = chunk.get('delta') or chunk.get('text') or ''
    elif etype == 'response.output_item.done':
        item = chunk.get('item') or {}
        itype = (item.get('type') or '').lower()
        if itype == 'message':
            for blk in item.get('content') or []:
                if isinstance(blk, dict) and blk.get('type') in ('output_text', 'text'):
                    content_delta += blk.get('text') or ''
        elif itype in ('reasoning', 'reasoning_summary'):
            c = item.get('content') or item.get('summary') or item.get('text')
            if isinstance(c, str):
                reasoning_delta += c
            elif isinstance(c, list):
                reasoning_delta += _extract_block_text(c)

    return content_delta, reasoning_delta


def _stream_responses(config, messages, max_tokens=None, temperature=None,
                      timeout=None):
    """Stream Responses API SSE and yield OQB delta/done events."""
    payload = _build_responses_payload(config, messages, max_tokens, temperature,
                                       stream=True)
    headers = _request_headers(config, stream=True)
    effective_timeout = int(timeout or config.timeout_seconds or 120)
    url = _api_url(config, 'responses')

    accumulated_content: list[str] = []
    accumulated_reasoning: list[str] = []
    finish_reason = None
    usage: dict = {}

    try:
        resp = requests.post(url, json=payload, headers=headers,
                             timeout=effective_timeout, stream=True)
    except requests.Timeout:
        raise LLMError(f'request timed out after {effective_timeout}s')
    except requests.RequestException as e:
        raise LLMError(f'request failed: {e}')

    with resp:
        if resp.status_code != 200:
            raise LLMError(f'HTTP {resp.status_code}: {resp.text[:500]}')
        resp.encoding = 'utf-8'

        content_type = (resp.headers.get('content-type') or '').lower()
        is_streaming = ('text/event-stream' in content_type
                        or 'application/x-ndjson' in content_type
                        or 'stream' in content_type)

        if not is_streaming:
            try:
                data = resp.json()
            except ValueError as e:
                raise LLMError(f'non-JSON response ({e}): {resp.text[:300]}')
            if isinstance(data, dict) and data.get('error') and not data.get('output'):
                _raise_api_error(data)
            text, reasoning, finish = _extract_responses_parts(data)
            yield {
                'type': 'done',
                'text': text or '',
                'reasoning': reasoning or '',
                'finish_reason': finish,
                'usage': data.get('usage') or {},
            }
            return

        current_event = ''
        for raw_line in resp.iter_lines(decode_unicode=True):
            if raw_line is None:
                continue
            line = raw_line.strip()
            if not line:
                current_event = ''
                continue
            if line.startswith(':'):
                continue
            if line.startswith('event:'):
                current_event = line[len('event:'):].strip().lower()
                continue
            if not line.startswith('data:'):
                continue
            payload_str = line[len('data:'):].strip()
            if payload_str == '[DONE]':
                break
            try:
                chunk = json.loads(payload_str)
            except ValueError:
                continue

            if isinstance(chunk, dict) and chunk.get('error'):
                _raise_api_error(chunk)

            if isinstance(chunk, dict) and chunk.get('usage'):
                usage = chunk['usage']

            if current_event and isinstance(chunk, dict) and not chunk.get('type'):
                chunk = dict(chunk)
                chunk['type'] = current_event

            if isinstance(chunk, dict) and chunk.get('type', '').endswith('completed'):
                finish_reason = chunk.get('status') or chunk.get('type')

            content_delta, reasoning_delta = _responses_stream_deltas(chunk)
            if content_delta or reasoning_delta:
                accumulated_content.append(content_delta)
                accumulated_reasoning.append(reasoning_delta)
                yield {
                    'type': 'delta',
                    'content': content_delta,
                    'reasoning': reasoning_delta,
                }

    yield {
        'type': 'done',
        'text': ''.join(accumulated_content),
        'reasoning': ''.join(accumulated_reasoning),
        'finish_reason': finish_reason,
        'usage': usage,
    }


def chat_messages_stream(config, messages, max_tokens=None, temperature=None,
                         timeout=None):
    """Generator that streams chunks from the endpoint's configured protocol.

    Yields:
    * ``{'type': 'delta', 'content': str, 'reasoning': str}``
    * ``{'type': 'done', 'text': str, 'reasoning': str, 'finish_reason': str,
       'usage': dict}``
    """
    if _api_protocol(config) == 'responses':
        yield from _stream_responses(config, messages, max_tokens, temperature,
                                     timeout)
    else:
        yield from _stream_chat_completions(config, messages, max_tokens,
                                            temperature, timeout)


def test_endpoint(config):
    """Send a trivial prompt to validate connectivity / auth / model.
    Returns ``(ok: bool, message: str)``."""
    try:
        text, info = chat(config, 'You are a connectivity test.',
                          "Reply with the single word: OK")
        text = (text or '').strip()
        if text:
            return True, text[:200]
        fr = (info or {}).get('finish_reason')
        return True, f'(connected, but empty reply; finish_reason={fr})'
    except LLMError as e:
        return False, str(e)
