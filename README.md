# Musicboard

Rate every song on the albums you listen to, from 0.5 to 10.

- Press **Add Album** and search for it. Pick it from the results and it's added with its cover and
  full tracklist, ready to score. Search uses Apple's free iTunes catalog, so it needs an internet connection.
- Open an album and type a score next to each song. **+ Add song** adds anything the tracklist is missing.
  Enter jumps from the name to the score, and Enter again starts the next song.
- Every score has a fixed color on a red (1) to green (10) scale.
- The dashboard groups albums by artist automatically. Press **Arrange** to drag
  albums within an artist, or drag whole artists up and down (arrow buttons work too).

## Accounts and public pages

- Everyone signs up with a **username and password**. Each person only sees their own albums.
- Boards are **private by default**. Click **Private** in the header and choose **Make public**,
  and anyone can see your board read-only at `/<your username>`, for example `http://127.0.0.1:5000/vineeth`.
- Click **Public** to copy your link or make the board private again.
- Albums saved before accounts existed are given to the first account that signs up.

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

## Letting other people use it

Running `python app.py` only serves your own computer. For friends to sign up or open your
`/username` link, the app has to run on a hosting service (Render, Railway, PythonAnywhere and similar).
When you host it, set two environment variables:

- `MUSICBOARD_SECRET` to a long random string (it signs login cookies)
- `MUSICBOARD_DEBUG=0` to turn off debug mode

## Files

| File | What it does |
| --- | --- |
| `app.py` | Flask server: accounts, SQLite database, public pages and the JSON API |
| `templates/index.html` | Your board, and other people's public pages |
| `templates/auth.html` | Log in and sign up |
| `templates/not_found.html` | Shown for private or unknown usernames |
| `templates/_title.html`, `_head.html` | Shared title (with the vinyl) and page head |
| `static/app.js` | Dashboard, album view, rating and drag-to-reorder logic |
| `static/style.css` | Styles (light and dark mode) |
