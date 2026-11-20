"""Musicboard: rate every song on the albums you listen to, 0.5 to 10.

Everyone signs up with a username and password. Albums are private by default;
switching a page to public makes /<username> show a read-only copy of that
person's dashboard.

Run with:  python app.py   then open http://127.0.0.1:5000
"""

import json
import math
import os
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, g, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("MUSICBOARD_DATA", BASE_DIR / "instance"))
DB_PATH = DATA_DIR / "musicboard.db"
COVER_DIR = DATA_DIR / "covers"
ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}

# Album search uses Apple's free iTunes Search API (no key needed).
ITUNES_API = os.environ.get("MUSICBOARD_ITUNES_API", "https://itunes.apple.com")
ITUNES_COUNTRY = os.environ.get("MUSICBOARD_COUNTRY", "US")

USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_.-]{1,28})[a-z0-9]$")
# Usernames that would clash with the app's own pages.
RESERVED_USERNAMES = {
    "about", "account", "admin", "api", "covers", "help", "login", "logout", "me",
    "musicboard", "new", "settings", "signup", "static", "u", "user", "users",
}

DATA_DIR.mkdir(parents=True, exist_ok=True)
COVER_DIR.mkdir(parents=True, exist_ok=True)


def load_secret_key():
    """Signs login cookies. Set MUSICBOARD_SECRET when hosting; otherwise one is created on first run."""
    if os.environ.get("MUSICBOARD_SECRET"):
        return os.environ["MUSICBOARD_SECRET"]
    key_file = DATA_DIR / "secret_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32))
    return key_file.read_text().strip()


app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,  # 10 MB uploads
    SECRET_KEY=load_secret_key(),
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,  # stay logged in for 30 days
)


# ---------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    is_public      INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS artists (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER REFERENCES users(id),
    name      TEXT NOT NULL,
    key       TEXT NOT NULL,
    position  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, key)
);
CREATE TABLE IF NOT EXISTS albums (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id),
    title       TEXT NOT NULL,
    artist_id   INTEGER NOT NULL REFERENCES artists(id),
    cover       TEXT,
    position    INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS songs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id  INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    name      TEXT NOT NULL DEFAULT '',
    rating    REAL,
    position  INTEGER NOT NULL DEFAULT 0
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def columns(db, table):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        # Databases from the single-user version have no user_id columns. Upgrade them in place;
        # their albums are handed to the first account that signs up.
        existing = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "artists" in existing and "user_id" not in columns(db, "artists"):
            # Build the new table beside the old one, then swap names, so the albums table's
            # link to "artists" keeps pointing at the right table.
            db.executescript("""
                PRAGMA foreign_keys = OFF;
                CREATE TABLE artists_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id),
                    name TEXT NOT NULL, key TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (user_id, key));
                INSERT INTO artists_new (id, user_id, name, key, position)
                    SELECT id, NULL, name, key, position FROM artists;
                DROP TABLE artists;
                ALTER TABLE artists_new RENAME TO artists;
            """)
        if "albums" in existing and "user_id" not in columns(db, "albums"):
            db.execute("ALTER TABLE albums ADD COLUMN user_id INTEGER REFERENCES users(id)")
        db.executescript(SCHEMA)


# ---------------------------------------------------------------- accounts

def current_user():
    if "user" not in g:
        uid = session.get("uid")
        g.user = None
        if uid is not None:
            g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            if g.user is None:
                session.clear()
    return g.user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            if request.path.startswith("/api/"):
                return jsonify(error="Log in to make changes."), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def uid():
    return current_user()["id"]


def username_problem(name):
    """Return why a username can't be used, or None if it's fine."""
    if not USERNAME_RE.match(name):
        return "Usernames are 3 to 30 characters: lowercase letters, numbers, dots, dashes or underscores, starting and ending with a letter or number."
    if name in RESERVED_USERNAMES:
        return "That username is reserved. Pick another one."
    return None


def safe_next(target):
    return target if target and target.startswith("/") and not target.startswith("//") else url_for("index")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user():
        return redirect(url_for("index"))
    error = None
    username = (request.form.get("username") or "").strip().lower().lstrip("@")
    if request.method == "POST":
        password = request.form.get("password") or ""
        db = get_db()
        error = username_problem(username)
        if error:
            pass
        elif len(password) < 8:
            error = "Use a password of at least 8 characters."
        elif db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            error = "That username is taken. Try another one."
        else:
            first_user = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            cur = db.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), time.time()),
            )
            if first_user:  # albums saved before accounts existed belong to the first account
                db.execute("UPDATE artists SET user_id = ? WHERE user_id IS NULL", (cur.lastrowid,))
                db.execute("UPDATE albums SET user_id = ? WHERE user_id IS NULL", (cur.lastrowid,))
            db.commit()
            session.clear()
            session.permanent = True
            session["uid"] = cur.lastrowid
            return redirect(url_for("index"))
    return render_template("auth.html", mode="signup", error=error, username=username)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("index"))
    error = None
    username = (request.form.get("username") or "").strip().lower().lstrip("@")
    next_url = request.values.get("next") or ""
    if request.method == "POST":
        user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"], request.form.get("password") or ""):
            error = "That username and password don't match an account."
        else:
            session.clear()
            session.permanent = True
            session["uid"] = user["id"]
            return redirect(safe_next(next_url))
    return render_template("auth.html", mode="login", error=error, username=username, next=next_url)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/api/me")
@login_required
def me():
    u = current_user()
    return jsonify(username=u["username"], is_public=bool(u["is_public"]))


@app.put("/api/me/visibility")
@login_required
def set_visibility():
    """Body: {"is_public": true} or {"is_public": false}."""
    data = request.get_json(silent=True) or {}
    db = get_db()
    db.execute("UPDATE users SET is_public = ? WHERE id = ?", (1 if data.get("is_public") else 0, uid()))
    db.commit()
    g.pop("user", None)
    return me()


# ---------------------------------------------------------------- helpers

def artist_key(name):
    return " ".join((name or "").lower().split())


def clean_rating(value):
    """Return a rating between 0.5 and 10 in half steps, or None for blank."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        abort(400, "Rating must be a number from 0.5 to 10.")
    number = min(10.0, max(0.5, number))
    return math.floor(number * 2 + 0.5) / 2  # round halves up, same as the browser


def find_or_create_artist(db, user_id, name):
    name = " ".join((name or "").split()) or "Unknown artist"
    key = artist_key(name)
    row = db.execute("SELECT id FROM artists WHERE user_id = ? AND key = ?", (user_id, key)).fetchone()
    if row:
        return row["id"]
    pos = db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM artists WHERE user_id = ?", (user_id,)).fetchone()[0]
    cur = db.execute("INSERT INTO artists (user_id, name, key, position) VALUES (?, ?, ?, ?)", (user_id, name, key, pos))
    return cur.lastrowid


def remove_empty_artists(db, user_id):
    db.execute(
        "DELETE FROM artists WHERE user_id = ? AND id NOT IN (SELECT DISTINCT artist_id FROM albums)", (user_id,)
    )


def next_album_position(db, artist_id):
    return db.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM albums WHERE artist_id = ?", (artist_id,)
    ).fetchone()[0]


def save_cover(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    ext = ALLOWED_IMAGE_TYPES.get(file_storage.mimetype)
    if not ext:
        abort(400, "Cover must be a JPG, PNG, WebP or GIF image.")
    filename = uuid.uuid4().hex + ext
    file_storage.save(COVER_DIR / filename)
    return filename


def delete_cover(filename):
    if filename:
        try:
            (COVER_DIR / filename).unlink()
        except FileNotFoundError:
            pass


def album_json(db, row):
    songs = db.execute(
        "SELECT id, name, rating FROM songs WHERE album_id = ? ORDER BY position, id", (row["id"],)
    ).fetchall()
    return {
        "id": row["id"],
        "title": row["title"],
        "artist_id": row["artist_id"],
        "artist": row["artist"],
        "cover_url": f"/covers/{row['cover']}" if row["cover"] else None,
        "position": row["position"],
        "created_at": row["created_at"],
        "songs": [dict(s) for s in songs],
    }


def get_album_row(db, album_id):
    """The signed-in user's album, or 404 (also for someone else's album)."""
    row = db.execute(
        "SELECT albums.*, artists.name AS artist FROM albums JOIN artists ON artists.id = albums.artist_id "
        "WHERE albums.id = ? AND albums.user_id = ?",
        (album_id, uid()),
    ).fetchone()
    if row is None:
        abort(404, "Album not found.")
    return row


def owned_song(db, song_id):
    row = db.execute(
        "SELECT songs.id FROM songs JOIN albums ON albums.id = songs.album_id WHERE songs.id = ? AND albums.user_id = ?",
        (song_id, uid()),
    ).fetchone()
    if row is None:
        abort(404, "Song not found.")
    return row


def library_for(db, user_id):
    artists = [dict(r) for r in db.execute(
        "SELECT id, name, position FROM artists WHERE user_id = ? ORDER BY position, id", (user_id,))]
    rows = db.execute(
        "SELECT albums.*, artists.name AS artist FROM albums JOIN artists ON artists.id = albums.artist_id "
        "WHERE albums.user_id = ? ORDER BY albums.position, albums.created_at",
        (user_id,),
    ).fetchall()
    return {"artists": artists, "albums": [album_json(db, r) for r in rows]}


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(413)
@app.errorhandler(502)
def json_error(err):
    message = getattr(err, "description", "Request failed.")
    if err.code == 413:
        message = "That image is too large. Pick one under 10 MB."
    if request.path.startswith("/api/"):
        return jsonify(error=message), err.code
    if err.code == 404:
        return render_template("not_found.html"), 404
    return message, err.code


@app.context_processor
def template_helpers():
    def asset(filename):
        """Static file URL that changes whenever the file changes, so browsers never use a stale copy."""
        try:
            version = int((BASE_DIR / "static" / filename).stat().st_mtime)
        except OSError:
            version = 0
        return f"/static/{filename}?v={version}"
    return {"asset": asset, "me": current_user()}


# ---------------------------------------------------------------- pages

@app.get("/")
@login_required
def index():
    return render_template("index.html", mode="owner", page_user=current_user())


@app.get("/covers/<path:filename>")
def cover(filename):
    return send_from_directory(COVER_DIR, filename)


@app.get("/<username>")
def public_page(username):
    """Someone's public page. Private or unknown usernames get the same 'not found' page."""
    user = get_db().execute(
        "SELECT * FROM users WHERE username = ? AND is_public = 1", (username.lower(),)
    ).fetchone()
    if user is None:
        abort(404)
    return render_template("index.html", mode="public", page_user=user)


# ---------------------------------------------------------------- API

@app.get("/api/library")
@login_required
def library():
    return jsonify(library_for(get_db(), uid()))


@app.get("/api/u/<username>/library")
def public_library(username):
    db = get_db()
    user = db.execute("SELECT id FROM users WHERE username = ? AND is_public = 1", (username.lower(),)).fetchone()
    if user is None:
        abort(404, "This page is private or doesn't exist.")
    return jsonify(library_for(db, user["id"]))


@app.post("/api/albums")
@login_required
def create_album():
    db = get_db()
    title = (request.form.get("title") or "").strip()
    artist = (request.form.get("artist") or "").strip()
    if not title or not artist:
        abort(400, "Add both the album name and the artist.")
    cover_name = save_cover(request.files.get("cover"))
    artist_id = find_or_create_artist(db, uid(), artist)
    cur = db.execute(
        "INSERT INTO albums (user_id, title, artist_id, cover, position, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (uid(), title, artist_id, cover_name, next_album_position(db, artist_id), time.time()),
    )
    album_id = cur.lastrowid
    db.execute("INSERT INTO songs (album_id, name, rating, position) VALUES (?, '', NULL, 0)", (album_id,))
    db.commit()
    return jsonify(album_json(db, get_album_row(db, album_id))), 201


@app.patch("/api/albums/<int:album_id>")
@login_required
def update_album(album_id):
    db = get_db()
    row = get_album_row(db, album_id)
    data = request.get_json(silent=True) or {}
    if "title" in data:
        title = (data["title"] or "").strip() or "Untitled"
        db.execute("UPDATE albums SET title = ? WHERE id = ?", (title, album_id))
    if "artist" in data:
        new_artist_id = find_or_create_artist(db, uid(), data["artist"])
        if new_artist_id != row["artist_id"]:
            db.execute(
                "UPDATE albums SET artist_id = ?, position = ? WHERE id = ?",
                (new_artist_id, next_album_position(db, new_artist_id), album_id),
            )
            remove_empty_artists(db, uid())
    db.commit()
    return jsonify(album_json(db, get_album_row(db, album_id)))


@app.patch("/api/artists/<int:artist_id>")
@login_required
def rename_artist(artist_id):
    """Body: {"name": "New name"}. Every album by this artist shows the new name.
    Renaming to an artist you already have merges the two into one row."""
    db = get_db()
    artist = db.execute("SELECT * FROM artists WHERE id = ? AND user_id = ?", (artist_id, uid())).fetchone()
    if artist is None:
        abort(404, "Artist not found.")
    name = " ".join(((request.get_json(silent=True) or {}).get("name") or "").split())
    if not name:
        abort(400, "Artist name can't be empty.")
    key = artist_key(name)
    other = db.execute(
        "SELECT id FROM artists WHERE user_id = ? AND key = ? AND id != ?", (uid(), key, artist_id)
    ).fetchone()
    if other:
        start = next_album_position(db, other["id"])
        albums = db.execute(
            "SELECT id FROM albums WHERE artist_id = ? ORDER BY position, created_at", (artist_id,)
        ).fetchall()
        for i, a in enumerate(albums):
            db.execute("UPDATE albums SET artist_id = ?, position = ? WHERE id = ?", (other["id"], start + i, a["id"]))
        db.execute("UPDATE artists SET name = ? WHERE id = ?", (name, other["id"]))
        db.execute("DELETE FROM artists WHERE id = ?", (artist_id,))
    else:
        db.execute("UPDATE artists SET name = ?, key = ? WHERE id = ?", (name, key, artist_id))
    db.commit()
    return jsonify(library_for(db, uid()))


@app.post("/api/albums/<int:album_id>/cover")
@login_required
def replace_cover(album_id):
    db = get_db()
    row = get_album_row(db, album_id)
    new_name = save_cover(request.files.get("cover"))
    if not new_name:
        abort(400, "Choose an image to upload.")
    db.execute("UPDATE albums SET cover = ? WHERE id = ?", (new_name, album_id))
    db.commit()
    delete_cover(row["cover"])
    return jsonify(album_json(db, get_album_row(db, album_id)))


@app.delete("/api/albums/<int:album_id>")
@login_required
def delete_album(album_id):
    db = get_db()
    row = get_album_row(db, album_id)
    db.execute("DELETE FROM songs WHERE album_id = ?", (album_id,))
    db.execute("DELETE FROM albums WHERE id = ?", (album_id,))
    remove_empty_artists(db, uid())
    db.commit()
    delete_cover(row["cover"])
    return "", 204


# ---------------------------------------------------------------- album search (iTunes)

def itunes_get(path, **params):
    url = f"{ITUNES_API}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Musicboard/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code in (403, 429):  # Apple allows roughly 20 searches a minute
            abort(502, "Apple's music search is busy. Wait a minute and try again.")
        abort(502, "Album search isn't working right now. Try again, or add the album yourself.")
    except (urllib.error.URLError, TimeoutError, ValueError):
        abort(502, "Album search isn't reachable right now. Check your internet connection, or add the album yourself.")


def big_artwork(url, size=600):
    """iTunes artwork URLs end in e.g. 100x100bb.jpg; ask for a larger square instead."""
    return re.sub(r"/\d+x\d+bb\.(jpg|png)$", f"/{size}x{size}bb.jpg", url or "") or None


def download_cover(url):
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Musicboard/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if not resp.headers.get_content_type().startswith("image/"):
                return None
            data = resp.read(10 * 1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError):
        return None  # the album still gets added, just without a cover
    if len(data) > 10 * 1024 * 1024:
        return None
    filename = uuid.uuid4().hex + ".jpg"
    (COVER_DIR / filename).write_bytes(data)
    return filename


def albums_from_songs(query):
    """Albums that contain songs matching the search. Apple's album search can miss an artist's own
    albums (searching "blonde frank ocean" doesn't return Blonde), but its song search finds them."""
    albums = []
    for r in itunes_get("search", term=query, entity="song", limit=50, country=ITUNES_COUNTRY).get("results", []):
        if r.get("collectionId"):
            albums.append({
                "collectionId": r["collectionId"], "collectionName": r.get("collectionName"),
                "artistName": r.get("collectionArtistName") or r.get("artistName"),
                "releaseDate": r.get("releaseDate"), "trackCount": r.get("trackCount"),
                "collectionExplicitness": r.get("collectionExplicitness"), "artworkUrl100": r.get("artworkUrl100"),
            })
    return albums


JUNK_WORDS = ("tribute", "karaoke", "instrumental", "lullaby", "relaxing", "piano versions", "made famous", "cover versions")


def rank_albums(items, query):
    """Apple's search ranks lots of singles first. Put full albums whose title matches the search on top."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]

    def score(item):
        index, r = item
        title = (r.get("collectionName") or "").lower()
        artist = (r.get("artistName") or "").lower()
        points = sum(2 for w in words if w in title) + sum(1 for w in words if w in artist)
        if words and all(w in title or w in artist for w in words):
            points += 3  # every word of the search is in the title or artist
        if any(j in title and j not in query.lower() for j in JUNK_WORDS):
            points -= 5
        if title.endswith(" - single") or (r.get("trackCount") or 0) <= 2:
            points -= 4
        elif title.endswith(" - ep"):
            points -= 1
        return (-points, index)  # ties keep Apple's order

    return [r for _, r in sorted(enumerate(items), key=score)]


@app.get("/api/search")
@login_required
def search_albums():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify(results=[])
    albums = itunes_get("search", term=q, entity="album", limit=100, country=ITUNES_COUNTRY).get("results", [])
    albums += albums_from_songs(q)
    seen, unique = set(), []
    for r in albums:
        if r.get("collectionId") and r["collectionId"] not in seen:
            seen.add(r["collectionId"]); unique.append(r)
    results = []
    for r in rank_albums(unique, q)[:15]:
        if r.get("collectionType") not in (None, "Album") or not r.get("collectionId"):
            continue
        results.append({
            "id": r["collectionId"],
            "title": r.get("collectionName", ""),
            "artist": r.get("artistName", ""),
            "year": (r.get("releaseDate") or "")[:4],
            "tracks": r.get("trackCount"),
            "explicit": r.get("collectionExplicitness") == "explicit",
            "artwork": r.get("artworkUrl100"),
        })
    return jsonify(results=results)


@app.post("/api/albums/import")
@login_required
def import_album():
    """Body: {"id": <iTunes collection id>}. Creates the album with its full tracklist and cover."""
    data = request.get_json(silent=True) or {}
    try:
        collection_id = int(data.get("id"))
    except (TypeError, ValueError):
        abort(400, "Pick an album from the search results.")
    found = itunes_get("lookup", id=collection_id, entity="song", country=ITUNES_COUNTRY).get("results", [])
    album = next((r for r in found if r.get("wrapperType") == "collection"), None)
    if album is None:
        abort(404, "That album couldn't be found anymore. Try searching again.")
    tracks = sorted(
        (r for r in found if r.get("wrapperType") == "track" and r.get("kind", "song") == "song"),
        key=lambda r: (r.get("discNumber") or 1, r.get("trackNumber") or 0),
    )
    db = get_db()
    artist_id = find_or_create_artist(db, uid(), album.get("artistName") or "Unknown artist")
    cover_name = download_cover(big_artwork(album.get("artworkUrl100")))
    cur = db.execute(
        "INSERT INTO albums (user_id, title, artist_id, cover, position, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (uid(), (album.get("collectionName") or "Untitled").strip(), artist_id, cover_name,
         next_album_position(db, artist_id), time.time()),
    )
    album_id = cur.lastrowid
    names = [(t.get("trackName") or "").strip() for t in tracks] or [""]
    db.executemany(
        "INSERT INTO songs (album_id, name, rating, position) VALUES (?, ?, NULL, ?)",
        [(album_id, name, i) for i, name in enumerate(names)],
    )
    db.commit()
    return jsonify(album_json(db, get_album_row(db, album_id))), 201


@app.post("/api/albums/<int:album_id>/songs")
@login_required
def add_song(album_id):
    db = get_db()
    get_album_row(db, album_id)
    data = request.get_json(silent=True) or {}
    pos = db.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM songs WHERE album_id = ?", (album_id,)
    ).fetchone()[0]
    name, rating = (data.get("name") or "").strip(), clean_rating(data.get("rating"))
    cur = db.execute(
        "INSERT INTO songs (album_id, name, rating, position) VALUES (?, ?, ?, ?)", (album_id, name, rating, pos)
    )
    db.commit()
    return jsonify(id=cur.lastrowid, name=name, rating=rating), 201


@app.patch("/api/songs/<int:song_id>")
@login_required
def update_song(song_id):
    db = get_db()
    owned_song(db, song_id)
    data = request.get_json(silent=True) or {}
    if "name" in data:
        db.execute("UPDATE songs SET name = ? WHERE id = ?", ((data["name"] or "").strip(), song_id))
    if "rating" in data:
        db.execute("UPDATE songs SET rating = ? WHERE id = ?", (clean_rating(data["rating"]), song_id))
    db.commit()
    return jsonify(dict(db.execute("SELECT id, name, rating FROM songs WHERE id = ?", (song_id,)).fetchone()))


@app.delete("/api/songs/<int:song_id>")
@login_required
def delete_song(song_id):
    db = get_db()
    owned_song(db, song_id)
    db.execute("DELETE FROM songs WHERE id = ?", (song_id,))
    db.commit()
    return "", 204


@app.put("/api/order")
@login_required
def save_order():
    """Body: {"artists": [artist ids in order]} and/or {"albums": [album ids in order]}."""
    db = get_db()
    data = request.get_json(silent=True) or {}
    for i, artist_id in enumerate(data.get("artists") or []):
        db.execute("UPDATE artists SET position = ? WHERE id = ? AND user_id = ?", (i, int(artist_id), uid()))
    for i, album_id in enumerate(data.get("albums") or []):
        db.execute("UPDATE albums SET position = ? WHERE id = ? AND user_id = ?", (i, int(album_id), uid()))
    db.commit()
    return "", 204


init_db()

if __name__ == "__main__":
    app.run(debug=os.environ.get("MUSICBOARD_DEBUG", "1") == "1")
