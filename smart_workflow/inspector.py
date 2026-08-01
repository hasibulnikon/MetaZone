"""Stage 2 — AI Quality Inspection.

Sends each preview to the same failover-capable AI engine already used
for metadata generation (engine.ai_providers.call_with_failover) with a
classification prompt instead of a metadata prompt, and parses the
result into one of three labels plus a confidence score. Nothing here
ever rejects an image outright — classification only, per spec.
"""
import re

LABELS = ("good", "review", "rejected")

INSPECTION_PROMPT = """You are a strict quality-control inspector for stock photo/illustration submissions.

Look at this image and check for ALL of the following problems:
- Severe blur or very low image quality
- AI-generation artifacts
- Deformed or malformed faces or hands
- Missing or duplicated body parts
- Visible logos, trademarks, watermarks, or signatures
- Visible text overlays
- Copyright-sensitive content (recognizable branded products, characters, or people)
- Any other major visual defect

Respond in EXACTLY this format, nothing else:
STATUS: GOOD or REVIEW or REJECTED
CONFIDENCE: <a number 0-100>
ISSUES: <comma-separated short issue names, or "none">

Use REJECTED only for severe/obvious defects. Use REVIEW when something is
questionable but not clearly disqualifying. Use GOOD when the image is clean."""


def parse_inspection(raw):
    """Returns (label, confidence:int, issues:str). Falls back to a
    conservative 'review' classification if the model's output doesn't
    match the expected format, rather than silently guessing 'good'."""
    label = "review"
    confidence = 50
    issues = ""
    m = re.search(r"STATUS:\s*(GOOD|REVIEW|REJECTED)", raw, re.IGNORECASE)
    if m:
        label = m.group(1).lower()
    c = re.search(r"CONFIDENCE:\s*(\d{1,3})", raw, re.IGNORECASE)
    if c:
        confidence = max(0, min(100, int(c.group(1))))
    i = re.search(r"ISSUES:\s*(.+)", raw, re.IGNORECASE)
    if i:
        issues = i.group(1).strip().splitlines()[0].strip()
    return label, confidence, issues


def inspect_one(preview_path, prefs, status_cb=None):
    """Runs Stage 2 for a single preview. Returns (label, confidence, issues).
    Raises on total API failure (all providers/keys failed) — caller
    decides how to handle that (Smart Workflow treats an inspection
    failure as 'review' rather than blocking the whole batch)."""
    from engine.ai_providers import call_with_failover
    raw, provider, model_id, key_idx = call_with_failover(
        preview_path, INSPECTION_PROMPT, prefs, status_cb=status_cb)
    return parse_inspection(raw)
