# index.py
#
# FastAPI app ready to deploy on Render (or run locally).
# Core parsing logic has been updated based on the app.py script.
#
# Requirements:
#   fastapi
#   uvicorn
#   python-multipart
#
# Recommended Render start command:
#   gunicorn -w 4 -k uvicorn.workers.UvicornWorker index:app

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
# CSS curly braces {} are escaped by doubling them to {{}} so Python's .format() ignores them.
HTML_UI = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Pokémon Parser</title>
<style>
  body{{font-family:Inter,Arial,sans-serif;margin:22px;background:#f6f7fb;color:#111}}
  .card{{background:#fff;padding:14px 22px;border-radius:8px;box-shadow:0 2px 6px rgba(0,0,0,0.06);max-width:900px}}
  pre{{white-space:pre-wrap;font-family:monospace;background:#f8f9fb;padding:10px;border-radius:6px;border:1px solid #e1e4e8}}
  textarea{{width:100%;height:160px;font-family:monospace;padding:8px;border-radius:6px;border:1px solid #ccc}}
  .row{{display:flex;gap:8px;align-items:center;margin-bottom:12px}}
  .hint{{color:#666;font-size:13px;margin-top:8px}}
  button{{font-size:14px;padding:8px 14px;border-radius:6px;border:1px solid #ccc;cursor:pointer}}
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
      <div>
        <label><strong>Or paste message text (used if no file is uploaded):</strong></label>
        <textarea name="text" placeholder="Paste the Pokétwo message here..."></textarea>
      </div>
      <div class="hint">
        Notes: Extracts names from Pokétwo-style messages. Deduplication is case-insensitive, but preserves the first-seen spelling.
      </div>
    </form>

    <h3>Result:</h3>
    <pre>{result_block}</pre>
  </div>
</body></html>
"""

# --- Core Logic from app.py ---

# Primary regex: look for the sparkle emoji then capture everything up to the next colon.
RE_SPARK = re.compile(r"✨\s*([^:]+?)\s*:", flags=re.UNICODE)

# Fallback regex: capture "<Name> : male/female/unknown" even if sparkle is missing.
RE_FALLBACK = re.compile(
    r"([A-Za-z0-9\u00C0-\u017F'’\-\.\s]+?)\s*:\s*(?:male|female|unknown|♂️|♀️)",
    flags=re.IGNORECASE | re.UNICODE,
)

def extract_names(text: str) -> Tuple[List[str], List[str]]:
    """
    Extract Pokémon names from Pokétwo-like message text using a two-pass strategy.

    Returns a tuple containing:
    - A list of unique Pokémon names, preserving first-seen casing.
    - A list of formatted strings for duplicates (e.g., "Pikachu: 2").
    """
    names = []

    # 1) Primary pass: "✨ Name:" (most reliable)
    for m in RE_SPARK.finditer(text):
        name = m.group(1).strip()
        if name:
            names.append(name)

    # 2) Fallback pass: find names before ": male/female/unknown"
    #    Avoid re-adding names already captured by the primary pass.
    existing_lowers = {n.lower() for n in names}
    for m in RE_FALLBACK.finditer(text):
        name = m.group(1).strip()
        if name and name.lower() not in existing_lowers:
            # Additional guard to ignore common non-Pokémon words
            if name.lower() in {"lvl", "your", "pokétwo", "app", "showing", "entries", "out", "of"}:
                continue
            names.append(name)
            existing_lowers.add(name.lower())

    # 3) Deduplicate: keep original order and preserve first-seen casing
    seen_ordered = OrderedDict()
    for n in names:
        key = n.lower()
        if key not in seen_ordered:
            seen_ordered[key] = n

    unique_list = list(seen_ordered.values())

    # 4) Count duplicates based on all occurrences found
    lower_occurrences = Counter([n.lower() for n in names])
    duplicate_lines = []
    for key, cnt in sorted(lower_occurrences.items()):
        if cnt > 1:
            # Use the preserved-casing version of the name for the output
            original_casing_name = seen_ordered[key]
            duplicate_lines.append(f"{original_casing_name}: {cnt - 1}")

    return unique_list, duplicate_lines


# --- Middleware for Error Handling ---
@app.middleware("http")
async def catch_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        tb = traceback.format_exc()
        logger.error("Unhandled exception:\n%s", tb)
        if os.environ.get("DEBUG") == "1" or request.query_params.get("debug") == "1":
            return PlainTextResponse(tb, status_code=500)
        return PlainTextResponse("Internal Server Error", status_code=500)


# --- API Routes ---
@app.get("/", response_class=HTMLResponse)
async def form():
    return HTML_UI.format(result_block="Upload a .txt file or paste the Pokétwo message and click 'Upload & Process'")

@app.post("/", response_class=HTMLResponse)
async def upload(file: Optional[UploadFile] = File(None), text: Optional[str] = Form(None)):
    """
    Handles file upload or pasted text, processes it, and returns the results.
    """
    content = ""
    if file and file.filename:
        try:
            b = await file.read()
            content = b.decode("utf-8", errors="ignore")
        except Exception:
            logger.exception("Failed to read uploaded file; falling back to text field")
            content = text or ""
    else:
        content = text or ""

    if not content:
        result_block = "No content provided. Please upload a file or paste text."
        return HTML_UI.format(result_block=escape(result_block))

    unique_names, duplicate_lines = extract_names(content)

    # Build the final plain text output block
    lines = []
    if unique_names:
        lines.extend(unique_names)
    else:
        lines.append("(no names found)")

    lines.append("")  # Blank line separator
    lines.append("Duplicates:")
    if duplicate_lines:
        lines.extend(duplicate_lines)
    else:
        lines.append("(none)")

    result_block = escape("\n".join(lines))
    return HTML_UI.format(result_block=result_block)


# --- Local Server Runner ---
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    reload = os.environ.get("DEBUG") == "1"
    uvicorn.run("index:app", host="0.0.0.0", port=port, reload=reload)
