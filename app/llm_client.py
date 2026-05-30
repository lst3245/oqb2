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
import mimetypes
import os

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


# ==================== Chat completion ====================

def chat(config, system: str, user_text: str, images=None):
    """Call ``{base_url}/chat/completions`` and return ``(text, usage)``.

    ``images`` is a list of ``(b64, mime)`` tuples (from ``prepare_image``)
    appended as image_url content blocks after the user text. Raises
    ``LLMError`` on any failure.
    """
    images = images or []
    content = [{'type': 'text', 'text': user_text}]
    for b64, mime in images:
        content.append({
            'type': 'image_url',
            'image_url': {'url': f'data:{mime};base64,{b64}'},
        })

    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': content})

    payload = {
        'model': config.model_name,
        'messages': messages,
        'max_tokens': int(config.max_output_tokens or 4096),
        'temperature': float(config.temperature or 0.0),
    }

    headers = {'Content-Type': 'application/json'}
    api_key = resolve_api_key(config)
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    url = config.base_url.rstrip('/') + '/chat/completions'
    try:
        resp = requests.post(url, json=payload, headers=headers,
                             timeout=int(config.timeout_seconds or 120))
    except requests.Timeout:
        raise LLMError(f'request timed out after {config.timeout_seconds}s')
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
