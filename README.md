# Musicboard

Rate every song on the albums you listen to, from 1 to 10.

- Press **+** to add an album: name, artist and an optional cover picture.
- Open an album and press **+ Add song** to type a song name and score it right next to it.
  Enter jumps from the name to the score, and Enter again starts the next song.
- Every score has a fixed color on a red (1) to green (10) scale.
- The dashboard groups albums by artist automatically. Press **Arrange** to drag
  albums within an artist, or drag whole artists up and down (arrow buttons work too).

Built with Python (Flask) and SQLite. Your data is saved in `instance/musicboard.db`
and covers in `instance/covers/`. That folder is ignored by git, so your ratings stay on your computer.

## Run it

You need Python 3.9 or newer.

```bash
python -m venv .venv
```

Activate it. On macOS or Linux:

```bash
source .venv/bin/activate
```

On Windows (PowerShell):

```bash
.venv\Scripts\Activate.ps1
```

Then:

```bash
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 in your browser.

In VS Code you can also press **F5** and pick **Run Musicboard**.

## Files

| File | What it does |
| --- | --- |
| `app.py` | Flask server, SQLite database and the JSON API |
| `templates/index.html` | The page |
| `static/app.js` | Dashboard, album view, rating and drag-to-reorder logic |
| `static/style.css` | Styles (light and dark mode) |
