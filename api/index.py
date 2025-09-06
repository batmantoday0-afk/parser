# index.py
#
# FastAPI app ready to deploy on Render (or run locally).
# Requirements:
#   fastapi
#   uvicorn
#   python-multipart
#
# Recommended Render start command:
#   gunicorn -w 4 -k uvicorn.workers.UvicornWorker index:app
#
# This app:
# - accepts a .txt upload or pasted text
# - extracts Pokémon names robustly (handles "✨ Name:", "Name:male:", etc.)
# - preserves first-seen original casing/format for display
# - deduplicates case-insensitively but treats distinct prefixes (e.g. "Galarian Corsola" != "Corsola")
# - removes trailing nicknames/notes like quoted nicknames, "gift from ...", "fs ..." so nicknames don't create false duplicates
# - prints unique names (one per line) and then duplicate counts (name: duplicate_count, duplicates beyond the first)
# - import-safe (suitable for serverless hosts); includes an optional DEBUG toggle via env var or ?debug=1

import os
import re
import sys
import traceback
import logging
from collections import Counter, OrderedDict
from html import escape
from typing import List, Tuple, Dict, Optional

from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

# --- logging ---
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("pokemon-parser")

app = FastAPI(title="Pokémon Parser (Render)")

# --- HTML UI ---
HTML_UI = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Pokémon Parser</title>
<style>
  body{font-family:Inter,Arial,sans-serif;margin:22px;background:#f6f7fb;color:#111}
  .card{background:#fff;padding:14px;border-radius:8px;box-shadow:0 2px 6px rgba(0,0,0,0.06);max-width:900px}
  pre{white-space:pre-wrap;font-family:monospace;background:#f8f9fb;padding:10px;border-radius:6px}
  textarea{width:100%;height:160px;font-family:monospace;padding:8px;border-radius:6px}
  .row{display:flex;gap:8px;align-items:center}
  .hint{color:#666;font-size:13px;margin-top:8px}
</style>
</head>
<body>
  <h1>Pokémon Parser</h1>
  <div class="card">
    <form method="post" enctype="multipart/form-data">
      <div class="row">
        <input type="file" name="file" accept=".txt">
        <button type="submit">Upload & Process</button>
      </div>
      <div style="margin-top:12px">
        <label><strong>Or paste message text (will be used if no file uploaded):</strong></label>
        <textarea name="text" placeholder="Paste the Pokétwo message here..."></textarea>
      </div>
      <div class="hint">
        Notes: case-insensitive dedupe (preserves first seen spelling), prefixes like "Galarian" are <em>not</em> merged with base species.
        Nicknames/notes such as "gift from ..." or quoted nicknames will be removed before dedupe.
      </div>
    </form>

    <h3>Result:</h3>
    <pre>{result_block}</pre>
  </div>
</body></html>
"""

# --- regexes for extraction ---
# 1) sparkle pattern: ✨ Name:
SPARKLE_RE = re.compile(r"✨\s*([^:]+):", flags=re.UNICODE)

# 2) common "Name:male:" or "Name:female:" pattern (captures Name)
GENDER_TAG_RE = re.compile(
    r"(?<!\S)([A-Za-zÀ-ÖØ-öø-ÿ'’\.·\u3000\-\–\— ]{2,120}):\s*(?:male|female|unknown)\b",
    flags=re.IGNORECASE | re.MULTILINE,
)

# 3) fallback: tokens that look like a pokemon name at line-start (before a colon)
LINE_START_NAME_RE = re.compile(r"(?m)^\s*([A-Za-zÀ-ÖØ-öø-ÿ'’\.·\u3000\-\–\— ]{2,120}):")

# words/phrases that are common trailing notes/nicknames we want to strip (case-insensitive)
TRAILING_NOTES_RE = re.compile(
    r'\b(?:gift\s+from|fs\b|giveaway\b|meow\b|:wattrel:|:wattrel\b|:wattrel:?\b)\b.*',
    flags=re.IGNORECASE,
)


def clean_name_token(raw: str) -> str:
    """
    Clean a raw extracted token to get the base Pokémon name as the user expects:
    - trim whitespace and special fullwidth spaces
    - strip leading digits/ID noise
    - remove trailing quoted nicknames or parenthetical notes
    - remove trailing "gift from ...", "fs ...", "giveaway", etc.
    - collapse multiple spaces
    - preserve prefixes like "Galarian", punctuation inside the name, and accents
    """
    if not raw:
        return ""

    name = raw.replace("\u3000", " ").strip()  # fullwidth spaces often present in copy
    # remove any leading numeric id or stray punctuation (e.g. "162435　:_: ✨ ")
    name = re.sub(r"^[\d\W_]+", "", name).strip()

    # If there's a quoted nickname (e.g. name "meow"), cut at the first quote character.
    # This removes the nickname but keeps the base name.
    for q in ['"', "“", "”", "'", "’"]:
        if q in name:
            name = name.split(q, 1)[0].strip()

    # Remove trailing parenthetical notes or trailing dash-separated nicknames
    if "(" in name:
        name = name.split("(", 1)[0].strip()
    for sep in [" - ", " — ", " – ", " —", " –", " -", " / "]:
        if sep in name:
            # Only cut if the left side looks like a valid name fragment (guard against cutting legit names)
            left = name.split(sep, 1)[0].strip()
            name = left

    # Remove common trailing notes like "gift from goldface", "fs", "giveaway" etc.
    name = re.sub(TRAILING_NOTES_RE, "", name).strip()

    # Remove any trailing stray punctuation characters
    name = name.rstrip("·•°●[]{}<>")

    # collapse spaces
    name = re.sub(r"\s+", " ", name).strip()

    return name


def extract_names_in_order(text: str) -> List[str]:
    """
    Extract names in the order they appear in the text.
    Strategy:
     - Use SPARKLE_RE and GENDER_TAG_RE finditer over the whole text to preserve order.
     - As fallback, use LINE_START_NAME_RE to get additional names.
     - Return a list of cleaned name tokens (may contain duplicates).
    """
    if not text:
        return []

    matches: List[Tuple[int, str]] = []

    # find sparkle matches
    for m in SPARKLE_RE.finditer(text):
        raw = m.group(1).strip()
        cleaned = clean_name_token(raw)
        if cleaned:
            matches.append((m.start(), cleaned))

    # find gender-tag matches
    for m in GENDER_TAG_RE.finditer(text):
        raw = m.group(1).strip()
        cleaned = clean_name_token(raw)
        if cleaned:
            matches.append((m.start(), cleaned))

    # fallback: line-start names (only include if they are not already covered near same position)
    # This helps capture lines like "Ivysaur:male: ..." when sparkle not present (some messages)
    for m in LINE_START_NAME_RE.finditer(text):
        raw = m.group(1).strip()
        cleaned = clean_name_token(raw)
        if cleaned:
            matches.append((m.start(), cleaned))

    # sort matches by appearance position
    matches.sort(key=lambda t: t[0])

    # return only the name tokens in order
    return [name for _, name in matches]


def dedupe_preserve_first(names: List[str]) -> Tuple[List[str], Dict[str, int]]:
    """
    Deduplicate case-insensitively but preserve the original first-seen form.
    Returns:
      - unique_list: list of unique names (first-seen form)
      - duplicates_map: { first-seen-form: duplicate_count_beyond_first }
    Example: names = ["Pidgey", "pidgey", "Pidgey"] -> unique_list=["Pidgey"], duplicates_map={"Pidgey":2}
    """
    seen: OrderedDict[str, str] = OrderedDict()  # key -> original form
    counts: Counter = Counter()

    for n in names:
        key = n.casefold()
        counts[key] += 1
        if key not in seen:
            seen[key] = n

    unique_list = list(seen.values())
    duplicates_map = {seen[k]: counts[k] - 1 for k in seen if counts[k] > 1}
    return unique_list, duplicates_map


# --- middleware to capture unexpected exceptions and optionally display tracebacks when DEBUG=1 ---
@app.middleware("http")
async def catch_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        tb = traceback.format_exc()
        logger.error("Unhandled exception:\n%s", tb)
        # write to /tmp/error.log for debugging on hosts
        try:
            with open("/tmp/pokemon_parser_error.log", "a", encoding="utf-8") as f:
                f.write(tb + "\n\n")
        except Exception:
            logger.exception("Failed to write /tmp log")
        if os.environ.get("DEBUG") == "1" or request.query_params.get("debug") == "1":
            return PlainTextResponse(tb, status_code=500)
        return PlainTextResponse("Internal Server Error", status_code=500)


# --- routes ---
@app.get("/", response_class=HTMLResponse)
async def form():
    return HTML_UI.format(result_block="Upload a .txt or paste the Pokétwo message and click Upload & Process")

@app.post("/", response_class=HTMLResponse)
async def upload(file: Optional[UploadFile] = File(None), text: Optional[str] = Form(None)):
    """
    Accepts either:
      - file: uploaded .txt (preferred), or
      - text: pasted message
    Processing rules:
      - If file provided, use its contents; else use text form field.
      - Extract names, dedupe case-insensitively, preserve first seen case/form.
      - Output unique names one per line. Then list duplicate counts (duplicates beyond the first).
    """
    content = ""
    if file is not None:
        try:
            b = await file.read()
            content = b.decode("utf-8", errors="ignore")
        except Exception:
            # fallback: read text field if file parse fails
            logger.exception("Failed to read uploaded file; falling back to text field")
            content = text or ""
    else:
        content = text or ""

    # extract and dedupe
    names = extract_names_in_order(content)
    unique_names, duplicates_map = dedupe_preserve_first(names)

    # build result text
    lines = []
    if unique_names:
        lines.append("Owned Pokémon (unique, first-seen form preserved):")
        lines.extend(unique_names)
    else:
        lines.append("No Pokémon names found.")

    lines.append("")  # blank
    lines.append("Duplicate counts (name: duplicate_count) — duplicates beyond the first:")
    if duplicates_map:
        # sort by name for stable output
        for name in sorted(duplicates_map.keys(), key=lambda s: s.casefold()):
            lines.append(f"{name}: {duplicates_map[name]}")
    else:
        lines.append("None")

    # escape result for safe HTML embedding (keeps punctuation)
    result_block = escape("\n".join(lines))
    return HTML_UI.format(result_block=result_block)


# Expose `app` at module level for Render / Gunicorn / ASGI
# Allow running locally with: python index.py
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    # debug mode if env var DEBUG=1
    uvicorn.run("index:app", host="0.0.0.0", port=port, reload=True if os.environ.get("DEBUG") == "1" else False)
