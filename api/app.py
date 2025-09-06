# api/index.py
from fastapi import FastAPI, File, UploadFile, Request, Form
from fastapi.responses import HTMLResponse, PlainTextResponse
import re
from collections import Counter, OrderedDict
import uvicorn

app = FastAPI()

HTML_FORM = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Pokémon Parser</title>
  <style>
    body { font-family: Inter, Arial, sans-serif; margin: 24px; background: #f6f7fb; color: #111; }
    h1 { margin-bottom: 8px; }
    form { margin-bottom: 18px; }
    .card { background: #fff; border-radius: 8px; padding: 14px; box-shadow: 0 2px 6px rgba(0,0,0,0.06); }
    .output { white-space: pre-wrap; font-family: monospace; margin-top: 12px; }
    button { padding: 8px 12px; }
  </style>
</head>
<body>
  <h1>Pokémon Parser</h1>
  <div class="card">
    <form method="post" enctype="multipart/form-data">
      <input type="file" name="file" accept=".txt" required>
      <button type="submit">Upload & Process</button>
    </form>
    <p>Result:</p>
    {result_block}
  </div>
</body>
</html>
"""

# Primary regex: sparkle-based capture "✨ Name:" (safe)
RE_SPARK = re.compile(r"✨\s*([^:]+?)\s*:", flags=re.UNICODE)

# Fallback regex: capture "Name : male/female/unknown" if sparkle missing
RE_FALLBACK = re.compile(
    r"([A-Za-z0-9\u00C0-\u017F'’\-\.\s]+?)\s*:\s*(?:male|female|unknown|♂️|♀️)",
    flags=re.IGNORECASE | re.UNICODE,
)

def extract_names(text: str):
    """
    Return (unique_list, duplicates_list)
    - unique_list: first-seen original-cased names (order preserved)
    - duplicates_list: lines like "Name: N" where N is extra occurrences beyond first
    """
    names = []

    # 1) Primary pass: "✨ Name:"
    for m in RE_SPARK.finditer(text):
        name = m.group(1).strip()
        if name:
            names.append(name)

    # 2) Fallback: names before ": male/female/unknown" but avoid re-adding
    existing_lowers = {n.lower() for n in names}
    for m in RE_FALLBACK.finditer(text):
        name = m.group(1).strip()
        if not name:
            continue
        key = name.lower()
        if key in existing_lowers:
            continue
        # guard against capturing words like "Lvl" etc.
        if key in {"lvl", "your", "pokétwo", "app", "showing", "entries", "out", "of"}:
            continue
        names.append(name)
        existing_lowers.add(key)

    # Preserve first-seen casing and order
    seen_ordered = OrderedDict()
    for n in names:
        k = n.lower()
        if k not in seen_ordered:
            seen_ordered[k] = n
    unique_list = list(seen_ordered.values())

    # Count occurrences from the names list (which includes repeats from capture)
    lower_occurrences = Counter([n.lower() for n in names])
    duplicates = []
    for key, cnt in lower_occurrences.items():
        if cnt > 1:
            duplicates.append(f"{seen_ordered[key]}: {cnt - 1}")

    return unique_list, duplicates

@app.get("/", response_class=HTMLResponse)
async def homepage():
    return HTML_FORM.format(result_block="")

@app.post("/", response_class=HTMLResponse)
async def upload(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        text = raw.decode("utf-8", errors="ignore")
    except Exception:
        text = str(raw)

    unique, dup_lines = extract_names(text)

    if unique:
        body = "<pre>" + "\n".join(unique) + "\n\nDuplicates:\n"
        if dup_lines:
            body += "\n".join(dup_lines)
        else:
            body += "(none)"
        body += "</pre>"
    else:
        body = "<pre>(no names found)</pre>"

    return HTML_FORM.format(result_block=body)

# For local debugging only
if __name__ == "__main__":
    uvicorn.run("api.index:app", host="127.0.0.1", port=8000, reload=True)
