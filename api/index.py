from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
import re
from collections import Counter

app = FastAPI()

HTML_FORM = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Pokémon Parser</title>
</head>
<body>
  <h1>Pokémon Parser</h1>
  <form method="post" enctype="multipart/form-data">
    <input type="file" name="file" accept=".txt" required>
    <button type="submit">Upload & Process</button>
  </form>
  <pre>{result_block}</pre>
</body>
</html>
"""

def extract_pokemon_names(text: str):
    names = []
    for line in text.splitlines():
        match = re.search(r"([A-Za-zÀ-ÖØ-öø-ÿ'´`’\\. -]+)", line)
        if match:
            raw_name = match.group(1).strip()
            if raw_name:
                names.append(raw_name)
    return names

@app.get("/", response_class=HTMLResponse)
async def form():
    return HTML_FORM.format(result_block="")

@app.post("/", response_class=HTMLResponse)
async def upload(file: UploadFile = File(...)):
    text = (await file.read()).decode("utf-8", errors="ignore")
    names = extract_pokemon_names(text)

    counts = Counter(names)
    unique_names = list(dict.fromkeys(names))

    # prepare output
    result = "\n".join(unique_names)
    result += "\n\nDuplicates:\n"
    for name, count in counts.items():
        if count > 1:
            result += f"{name}: {count - 1}\n"

    return HTML_FORM.format(result_block=result)
