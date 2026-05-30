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
    "- Write inline math as $...$ and display math as $$...$$ (LaTeX).\n"
    "- Preserve question/part numbering, lists, tables, and option labels "
    "(A/B/C/D) as Markdown structure.\n"
    "- Keep the original language (English and/or Chinese) exactly.\n"
    "- For diagrams, figures, graphs, or geometric drawings that cannot be "
    "expressed as text/LaTeX, insert a placeholder line on its own: "
    "[FIGURE: short description].\n"
    "- Output ONLY the Markdown for the question content. No code fences "
    "around the whole answer, no preamble, no explanation."
)


def build_md_user_text(source_version, asset_type):
    return (
        f"Transcribe this {asset_type} image (version {source_version}) into "
        f"Markdown following the rules. Output Markdown only."
    )


def strip_md_fences(text: str) -> str:
    """Remove a single wrapping ```...``` fence if the model added one
    around the whole answer despite instructions."""
    s = (text or '').strip()
    m = re.match(r'^```[a-zA-Z0-9_-]*\s*\n(.*)\n```$', s, re.DOTALL)
    if m:
        return m.group(1).strip()
    return s


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
