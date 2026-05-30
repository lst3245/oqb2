"""
Prompt templates + output parsing for the AI Tools feature.

Two operations:
  * CHECK  - proofread a typed question image against the official scan.
  * MD GEN - transcribe a question image into self-contained Markdown.
"""
import json
import re


# ==================== Image checking (proofreading) ====================

CHECK_SYSTEM = (
    "You are a meticulous bilingual (English/Chinese) exam-paper proofreader. "
    "You are given two images of the SAME exam question asset: an OFFICIAL "
    "scanned version (the ground truth) and a TYPED reproduction that may "
    "contain transcription mistakes. Compare them carefully and report every "
    "discrepancy in the TYPED version relative to the OFFICIAL one: typos, "
    "wrong or missing numbers, altered mathematical symbols/expressions, "
    "missing or extra words, wrong subscripts/superscripts, swapped options, "
    "and formatting errors that change meaning. Ignore differences that do "
    "not affect meaning (font, colour, resolution, scan artefacts, layout, "
    "page margins, watermarks).\n\n"
    "Respond with STRICT JSON only (no prose, no markdown fences) of the form:\n"
    '{"status": "ok"} when the typed version is faithful, OR\n'
    '{"status": "issues", "issues": [{"location": "<where>", '
    '"description": "<what is wrong>", "severity": "minor|major"}]}\n'
    "Use \"major\" when the mistake changes the meaning or the answer."
)


def build_check_user_text(typed_version, ref_version, asset_type):
    """The user-turn instruction accompanying the two images."""
    return (
        f"Asset type: {asset_type}. "
        f"The FIRST image(s) are the OFFICIAL scanned version ({ref_version}). "
        f"The following image(s) are the TYPED version ({typed_version}) to be "
        f"proofread. List discrepancies in the TYPED version. Return STRICT JSON."
    )


# ==================== Markdown generation ====================

MD_SYSTEM = (
    "You are an expert at transcribing exam questions from images into clean, "
    "self-contained GitHub-Flavored Markdown. Rules:\n"
    "- Transcribe the content faithfully and completely. Do NOT solve, answer, "
    "or add commentary.\n"
    "- Write inline math as $...$ and display math as $$...$$ (LaTeX). Put NO "
    "space immediately inside the inline dollar signs: write $x+1$, NEVER "
    "$ x+1 $. Spaced delimiters do not render.\n"
    "- Preserve question/part numbering, lists, tables, and option labels "
    "(A/B/C/D) as Markdown structure.\n"
    "- Keep the original language (English and/or Chinese) exactly.\n"
    "- ONLY for an actual diagram, figure, graph, chart, or geometric drawing "
    "that genuinely cannot be written as text or LaTeX, insert a placeholder "
    "line on its own: [FIGURE: short description]. Plain text, equations, "
    "tables, and multiple-choice options are NOT figures — never use the "
    "placeholder for them. If the question has no such drawing, do not emit "
    "any [FIGURE] placeholder at all.\n"
    "- Output ONLY the Markdown for the question content. No code fences "
    "around the whole answer, no preamble, no explanation."
)


def build_md_user_text(source_version, asset_type):
    return (
        f"Transcribe this {asset_type} image (version {source_version}) into "
        f"Markdown following the rules. Output Markdown only. Remember: only use "
        f"a [FIGURE: ...] placeholder if there is a real diagram/graph/drawing."
    )


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


FIGURE_BOX_SYSTEM = (
    "You are a precise vision tool that locates figures in an exam-question "
    "image. A 'figure' is a diagram, graph, chart, geometric drawing, or "
    "picture — NOT plain text, equations, tables, or multiple-choice options.\n"
    "Return STRICT JSON only (no prose, no markdown fences): a list of figures "
    "in reading order, each as {\"caption\": \"...\", \"box\": [x1, y1, x2, y2]} "
    "where the coordinates are FRACTIONS of the image size between 0 and 1 "
    "(x1,y1 = top-left corner, x2,y2 = bottom-right corner). If there are no "
    "figures, return []."
)

FIGURE_BOX_USER = (
    "List the bounding boxes of the real figures/diagrams in this image as "
    "STRICT JSON. Use fractional coordinates (0..1). Return [] if none."
)


def parse_figure_boxes(text: str):
    """Parse the figure-box model output into a list of
    ``{caption, box:[x1,y1,x2,y2]}`` with floats clamped to 0..1.

    Tolerant of fences/prose. Returns ``[]`` on any failure (caller then
    falls back to embedding the whole image).
    """
    if not text:
        return []
    candidates = [text.strip()]
    for m in re.finditer(r'```(?:json)?\s*(.*?)```', text, re.DOTALL):
        candidates.append(m.group(1).strip())
    arr = re.search(r'\[.*\]', text, re.DOTALL)
    if arr:
        candidates.append(arr.group(0))

    for c in candidates:
        try:
            data = json.loads(c)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            data = data.get('figures') or data.get('boxes') or []
        if not isinstance(data, list):
            continue
        out = []
        for it in data:
            if not isinstance(it, dict):
                continue
            box = it.get('box') or it.get('bbox') or it.get('bounding_box')
            if not (isinstance(box, (list, tuple)) and len(box) == 4):
                continue
            try:
                coords = [float(v) for v in box]
            except (ValueError, TypeError):
                continue
            # Some models answer in 0..1000 or pixel space — normalise > 1.
            if any(v > 1.0 for v in coords):
                m = max(coords) or 1.0
                scale = 1000.0 if m <= 1000 else m
                coords = [v / scale for v in coords]
            x1, y1, x2, y2 = (min(max(v, 0.0), 1.0) for v in coords)
            out.append({'caption': str(it.get('caption', '') or ''),
                        'box': [x1, y1, x2, y2]})
        return out
    return []


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
    candidates = []
    s = text.strip()
    candidates.append(s)
    # fenced ```json ... ```
    for m in re.finditer(r'```(?:json)?\s*(.*?)```', s, re.DOTALL):
        candidates.append(m.group(1).strip())
    # first {...} balanced-ish blob
    brace = re.search(r'\{.*\}', s, re.DOTALL)
    if brace:
        candidates.append(brace.group(0))

    for c in candidates:
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
