# api/index.py
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse
import re

app = FastAPI()

# Simple HTML form
HTML_FORM = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Pokémon Parser</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; background: #f6f7fb; }
    h1 { margin-bottom: 12px; }
    form { margin-bottom: 18px; }
    pre { background: #fff; padding: 12px; border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,0.1); }
    button { padding: 6px 12px; cursor: pointer; }
  </style>
</head>
<body>
  <h1>Pokémon Parser</h1>
  <form method="post" enctype="multipart/form-data">
    <input type="file" name="file" accept=".txt" required>
    <button type="submit">Upload & Process</button>
  </form>
  <h3>Result:</h3>
  <pre>{result_block}</pre>
</body>
</html>
"""

def parse_pokemon_names(content: str):
    """
    Extract Pokémon names from Pokétwo text.
    Strips nicknames like 'gift from goldface' and keeps only the actual Pokémon names.
    """
    # Match patterns like ✨ Bulbasaur:male:
    matches = re.findall(r"✨\s+([^\:]+):", content)

    clean_names = []
    for raw in matches:
        name = raw.strip()
        # If Pokémon has a nickname (contains extra words after base name), 
        # only take the first token before any quotes
        if '"' in name:
            name = name.split('"')[0].strip()
        if "gift from" in name.lower():
            name = name.split("gift from")[0].strip()
        clean_names.append(name)

    uniques, duplicates, seen = [], [], set()
    for name in clean_names:
        if name not in seen:
            seen.add(name)
            uniques.append(name)
        else:
            duplicates.append(name)

    return uniques, sorted(set(duplicates))

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTML_FORM.format(result_block="")

@app.post("/", response_class=HTMLResponse)
async def upload(file: UploadFile = File(...)):
    content = (await file.read()).decode("utf-8")
    uniques, duplicates = parse_pokemon_names(content)

    result = "Owned Pokémon:\n" + "\n".join(uniques)
    result += "\n\nDuplicates:\n" + ("\n".join(duplicates) if duplicates else "None")

    return HTML_FORM.format(result_block=result)
