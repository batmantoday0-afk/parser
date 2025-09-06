# api/index.py
import os
import re
import sys
import traceback
import logging
from collections import Counter, OrderedDict
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

# --- logging ---
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("parser")

app = FastAPI(title="Pokémon Parser")

# Simple HTML UI embedded (works on Vercel)
HTML_FORM = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Pokémon Parser</title>
<style>
  body{font-family:Inter,Arial,sans-serif;margin:22px;background:#f6f7fb}
  .card{background:#fff;padding:14px;border-radius:8px;box-shadow:0 2px 6px rgba(0,0,0,0.06)}
  pre{white-space:pre-wrap;font-family:monospace}
  button{padding:8px 12px}
</style>
</head>
<body>
  <h1>Pokémon Parser</h1>
  <div class="card">
    <form method="post" enctype="multipart/form-data">
      <input type="file" name="file" accept=".txt" required>
      <button type="submit">Upload & Process</button>
    </form>
    <h3>Result:</h3>
    <pre>{result_block}</pre>
  </div>
</body></html>
"""

# --- middleware: catch and log uncaught exceptions so Vercel logs show stacktrace ---
@app.middleware("http")
async def catch_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("Unhandled exception:\n%s", tb)
        # write to /tmp for quick retrieval from container logs
        try:
            with open("/tmp/error.log", "a", encoding="utf-8") as f:
                f.write(tb + "\n\n")
        except Exception:
            logger.exception("Could not write /tmp/error.log")
        # show full traceback in browser only if debug allowed
        if os.environ.get("DEBUG") == "1" or request.query_params.get("debug") == "1":
            # return plain trace for debugging (remove after fixing)
            return PlainTextResponse(tb, status_code=500)
        return PlainTextResponse("Internal Server Error", status_code=500)

# --- parsing helpers ---
SPARKLE_RE = re.compile(r"✨\s*([^:]+):")         # matches "✨ Name:" patterns
GENERIC_NAME_RE = re.compile(r"(?m)^[^\d\W][\w'’\.\- ]{1,60}")  # fallback: start-of-line non-digit

def clean_name_token(raw: str) -> str:
    """Keep base pokemon name and remove common trailing nick / notes.
       - remove anything starting with quotes (" or ') or parentheses or 'gift from' or 'fs'
       - preserve prefixes like 'Galarian' or 'Alolan' (do NOT strip them)
    """
    if not raw:
        return raw
    name = raw.strip()
    # Remove parts after quotes or parentheses or dashes (nicknames often quoted)
    for sep in ['"', "”", "“", "'", "’", "(", " - ", " — ", "–", "—"]:
        if sep in name:
            name = name.split(sep)[0].strip()
    # Remove common suffix notes
    name = re.sub(r'\bgift from\b.*', '', name, flags=re.I).strip()
    name = re.sub(r'\bfs\b.*', '', name, flags=re.I).strip()
    # Final cleanup
    return name.strip()

def extract_names_from_text(text: str):
    """
    Return list of raw names in order of appearance (before dedupe).
    Strategy:
      1) Find all "✨ Name:" occurrences (most reliable).
      2) If none found on a line, fallback to a capitalized token at line start.
    """
    names = []
    # 1) sparkle matches
    for m in SPARKLE_RE.finditer(text):
        token = m.group(1)
        token = clean_name_token(token)
        if token:
            names.append(token)

    # 2) fallback: scan lines for likely names if SPARKLE_RE yielded little
    if not names:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.lower().startswith(("showing","your pokémon","entries")):
                continue
            m = GENERIC_NAME_RE.search(line)
            if m:
                token = clean_name_token(m.group(0))
                if token:
                    names.append(token)
    return names

# --- routes ---
@app.get("/", response_class=HTMLResponse)
async def get_form():
    return HTML_FORM.format(result_block="")

@app.post("/", response_class=HTMLResponse)
async def upload(file: UploadFile = File(...)):
    # decode safely and avoid import-time work
    raw = (await file.read()).decode("utf-8", errors="ignore")
    names = extract_names_from_text(raw)

    # dedupe case-insensitively but preserve first-seen casing
    seen = OrderedDict()
    counts = Counter()
    for n in names:
        key = n.casefold()
        counts[key] += 1
        if key not in seen:
            seen[key] = n  # preserve first-seen original form

    unique_list = list(seen.values())
    duplicates_map = {seen[k]: counts[k] - 1 for k in seen if counts[k] > 1}

    # Build result text: unique lines, then duplicates counts
    result_lines = []
    if unique_list:
        result_lines.append("Owned Pokémon (unique, first occurrence preserved):")
        result_lines.extend(unique_list)
    else:
        result_lines.append("No Pokémon names found.")

    result_lines.append("")  # blank line
    result_lines.append("Duplicate counts (name: duplicate_count):")
    if duplicates_map:
        for nm, dupcount in sorted(duplicates_map.items()):
            result_lines.append(f"{nm}: {dupcount}")
    else:
        result_lines.append("None")

    return HTML_FORM.format(result_block="\n".join(result_lines))
