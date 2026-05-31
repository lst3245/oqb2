"""
OpenAI-compatible LLM client for the AI Tools admin feature.

Every configured endpoint (``app.models.LLMConfig``) speaks the OpenAI
Chat Completions protocol (``POST {base_url}/chat/completions``) with
base64 image input, so this single adapter serves both local servers
(Ollama, LM Studio, vLLM, ...) and cloud providers (OpenAI, OpenRouter,
...). Anthropic / Gemini are reachable via an OpenAI-compatible proxy.

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


# ==================== Chat completion ====================

def _image_block(b64, mime):
    """An OpenAI image_url content block from a ``(b64, mime)`` pair."""
    return {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}'}}


def _post_chat(config, messages, max_tokens=None, temperature=None, timeout=None):
    """POST a pre-built OpenAI ``messages`` array to
    ``{base_url}/chat/completions`` and return ``(text, info)``. Shared by
    ``chat`` (single-turn + images) and ``chat_messages`` (multi-turn).

    ``timeout`` (seconds) overrides ``config.timeout_seconds`` when set —
    used by interactive features (Explain, admin Chat console) to grant
    reasoning models extra wall-clock time without bumping the per-endpoint
    default that batch ops rely on.
    """
    payload = {
        'model': config.model_name,
        'messages': messages,
        'max_tokens': int(max_tokens or config.max_output_tokens or 4096),
        'temperature': float(config.temperature if temperature is None else temperature),
    }

    headers = {'Content-Type': 'application/json'}
    api_key = resolve_api_key(config)
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    effective_timeout = int(timeout or config.timeout_seconds or 120)
    url = config.base_url.rstrip('/') + '/chat/completions'
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

    # Some OpenAI-compatible servers wrap errors in a 200 body.
    if isinstance(data, dict) and data.get('error') and not data.get('choices'):
        err = data['error']
        msg = err.get('message') if isinstance(err, dict) else str(err)
        raise LLMError(f'API error: {msg}')

    text, finish = _extract_message_text(data)
    info = {
        'usage': data.get('usage') or {},
        'finish_reason': finish,
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
    return _post_chat(config, messages)


def chat_messages(config, messages, max_tokens=None, temperature=None, timeout=None):
    """Call ``{base_url}/chat/completions`` with a full pre-built OpenAI
    ``messages`` array (multi-turn conversation, optionally multimodal) and
    return ``(text, info)``. Used by the Explain tutor chat and the admin
    Chat console. ``timeout`` (seconds) overrides the endpoint's default
    when set — handy for reasoning models. Raises ``LLMError`` on any
    failure."""
    return _post_chat(config, messages, max_tokens=max_tokens,
                      temperature=temperature, timeout=timeout)


def chat_messages_stream(config, messages, max_tokens=None, temperature=None,
                         timeout=None):
    """Generator that streams chunks from ``{base_url}/chat/completions``.

    Asks the server for ``"stream": true`` and yields dicts as data arrives:

    * ``{'type': 'delta', 'content': str, 'reasoning': str}`` — incremental
      tokens. Either ``content`` or ``reasoning`` may be empty for a given
      chunk; both never simultaneously empty.
    * ``{'type': 'done', 'text': str, 'reasoning': str, 'finish_reason': str,
       'usage': dict}`` — emitted exactly once at end-of-stream.

    Raises ``LLMError`` on transport / HTTP / shape failures.

    Servers that don't actually stream (return a one-shot JSON body even when
    asked to stream) are handled gracefully — the full response is parsed and
    a single ``done`` event is yielded with the complete text.

    Streaming is the right choice when a reverse proxy sits between the
    browser and Flask: bytes flow continuously down the wire, so the proxy
    never sees an idle connection and never returns a 504.
    """
    payload = {
        'model': config.model_name,
        'messages': messages,
        'max_tokens': int(max_tokens or config.max_output_tokens or 4096),
        'temperature': float(config.temperature if temperature is None else temperature),
        'stream': True,
    }

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
    }
    api_key = resolve_api_key(config)
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    effective_timeout = int(timeout or config.timeout_seconds or 120)
    url = config.base_url.rstrip('/') + '/chat/completions'

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

        # Force UTF-8 decoding. JSON / SSE bodies are mandated to be UTF-8
        # (RFC 8259, RFC 8895), but many LLM servers (LM Studio, some Ollama
        # builds) forget to put ``charset=utf-8`` in Content-Type. Without an
        # explicit encoding, ``requests`` falls back to ISO-8859-1, which
        # mangles curly quotes / em-dashes / CJK / emoji into mojibake
        # (``â€œ``, ``ðŸ§®``) once the bytes are re-encoded as JSON further
        # down the chain.
        resp.encoding = 'utf-8'

        content_type = (resp.headers.get('content-type') or '').lower()
        is_streaming = ('text/event-stream' in content_type
                        or 'application/x-ndjson' in content_type
                        or 'stream' in content_type)

        if not is_streaming:
            # Server ignored stream:true — fall back to one-shot parse.
            try:
                data = resp.json()
            except ValueError as e:
                raise LLMError(f'non-JSON response ({e}): {resp.text[:300]}')
            if isinstance(data, dict) and data.get('error') and not data.get('choices'):
                err = data['error']
                msg = err.get('message') if isinstance(err, dict) else str(err)
                raise LLMError(f'API error: {msg}')
            text, finish = _extract_message_text(data)
            yield {
                'type': 'done',
                'text': text or '',
                'reasoning': '',
                'finish_reason': finish,
                'usage': data.get('usage') or {},
            }
            return

        for raw_line in resp.iter_lines(decode_unicode=True):
            if raw_line is None:
                continue
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(':'):
                continue  # SSE comment / heartbeat
            if not line.startswith('data:'):
                continue
            payload_str = line[len('data:'):].strip()
            if payload_str == '[DONE]':
                break
            try:
                chunk = json.loads(payload_str)
            except ValueError:
                continue

            # OpenAI errors-as-200 in a chunk.
            if isinstance(chunk, dict) and chunk.get('error') and not chunk.get('choices'):
                err = chunk['error']
                msg = err.get('message') if isinstance(err, dict) else str(err)
                raise LLMError(f'API error: {msg}')

            if isinstance(chunk, dict) and chunk.get('usage'):
                usage = chunk['usage']

            choices = (chunk.get('choices') or []) if isinstance(chunk, dict) else []
            if not choices:
                continue
            choice = choices[0] or {}
            delta = choice.get('delta') or {}
            content_delta = delta.get('content') or ''
            # Reasoning models send reasoning under various aliases.
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

    full_text = ''.join(accumulated_content)
    full_reasoning = ''.join(accumulated_reasoning)
    yield {
        'type': 'done',
        'text': full_text,
        'reasoning': full_reasoning,
        'finish_reason': finish_reason,
        'usage': usage,
    }


def _extract_message_text(data):
    """Pull assistant text out of an OpenAI-shaped response, tolerating
    content-as-blocks, reasoning-only replies, and completion-style ``text``.

    Returns ``(text, finish_reason)``.
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
        # Multimodal output: list of blocks like {type:'text', text:'...'}.
        parts = []
        for blk in content:
            if isinstance(blk, dict) and isinstance(blk.get('text'), str):
                parts.append(blk['text'])
            elif isinstance(blk, str):
                parts.append(blk)
        text = '\n'.join(parts)

    # Reasoning models sometimes leave content empty and put the answer in
    # a reasoning field — use it only as a last resort.
    if not (text or '').strip():
        for k in ('reasoning_content', 'reasoning'):
            v = msg.get(k)
            if isinstance(v, str) and v.strip():
                text = v
                break

    # Completion-style fallback (`choice.text`).
    if not (text or '').strip() and isinstance(choice.get('text'), str):
        text = choice['text']

    return text or '', finish


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
