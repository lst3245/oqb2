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


# ==================== Question explanation (AI tutor chat) ====================

EXPLAIN_SYSTEM = (
    "You are an expert, encouraging exam tutor helping a student understand ONE "
    "exam question. You are given image(s) of the QUESTION and, when available, "
    "its official SOLUTION. Explain it clearly and pedagogically:\n"
    "- First restate, in plain language, what the question is asking.\n"
    "- Explain the key concepts, then walk through the reasoning step by step, "
    "justifying each step rather than just stating it.\n"
    "- If a SOLUTION is provided, base your explanation on it and expand any "
    "terse steps; if not, work the problem out yourself and give the answer.\n"
    "- Use LaTeX for ALL mathematics: inline math as $...$ and display math as "
    "$$...$$, with NO space immediately inside the dollar signs (write $x+1$, "
    "NEVER $ x+1 $ — spaced delimiters do not render).\n"
    "- Keep the student's language (English and/or Chinese). Be concise but "
    "complete, and answer any follow-up questions in the same style."
)

# The user-turn instruction that accompanies the question/solution images.
EXPLAIN_INITIAL_USER = (
    "Please explain this question: what it is asking, the concepts involved, "
    "and how to arrive at the answer step by step."
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


# ==================== PDF batch import (question region detection) ====================

# Shared tail describing the STRICT JSON contract for both QUE and SOL prompts.
#
# Coordinate convention: an explicit 0-1000 integer grid with the ORIGIN at the
# TOP-LEFT and x BEFORE y, plus a worked numeric example. Vision models disagree
# wildly on box conventions (0..1 vs 0..1000 vs raw pixels; x-first vs y-first),
# so we pin one convention here and parse defensively in parse_question_boxes.
# Models that ignore this and answer y-first (Gemma/Gemini family) are handled
# by the PDF_IMPORT_COORD_ORDER='yxyx' setting.
_PDF_BOX_JSON_CONTRACT = (
    "Return STRICT JSON only (no prose, no markdown fences): a list, in "
    "top-to-bottom reading order, of objects of the form\n"
    '{"qno": <integer printed question number>, "box": [x1, y1, x2, y2], '
    '"continues_prev": <true|false>, "continues_next": <true|false>}\n'
    "COORDINATES: integers on a 0-1000 grid measured from the TOP-LEFT corner "
    "of the page. x is the HORIZONTAL position (x=0 is the left edge, x=1000 "
    "the right edge); y is the VERTICAL position (y=0 is the TOP edge, y=1000 "
    "the BOTTOM edge). The box is [x1, y1, x2, y2] where (x1,y1) is its "
    "TOP-LEFT corner and (x2,y2) its BOTTOM-RIGHT corner, so always x1 < x2 "
    "and y1 < y2. Example: a question that fills the TOP THIRD of the page "
    "across almost the full width is "
    '{"qno": 1, "box": [40, 70, 960, 330], "continues_prev": false, '
    '"continues_next": false} — note the small y values because it is near the '
    "TOP. \"qno\" is the PRINTED question number you can read on the page (an "
    "integer; for a part like \"5\" use 5). Set \"continues_prev\" to true when "
    "the topmost region is the tail of a question that began on the previous "
    "page, and \"continues_next\" to true when the bottom region is cut off and "
    "continues on the next page. If the page has no question content, return []."
)

PDF_QUE_BOX_SYSTEM = (
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
    + _PDF_BOX_JSON_CONTRACT
)

PDF_SOL_BOX_SYSTEM = (
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
    + _PDF_BOX_JSON_CONTRACT
)


def build_pdf_box_user_text(asset_type: str) -> str:
    """User-turn instruction accompanying a single page image."""
    what = 'questions' if asset_type == 'QUE' else 'solutions'
    return (
        f"List the bounding boxes of every {what} on this page as STRICT JSON. "
        f"Use integer coordinates on a 0-1000 grid in the order [x1, y1, x2, y2] "
        f"= [left, top, right, bottom], measured from the top-left corner. "
        f"Include the printed question number for each. Return [] if the page "
        f"has no {what}."
    )


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
            data = data.get('questions') or data.get('boxes') or data.get('regions') or []
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
