"""Musicboard: rate every song on the albums you listen to, 1 to 10.

Run with:  python app.py   then open http://127.0.0.1:5000
"""

import os
import sqlite3
import time
import uuid
from pathlib import Path

from flask import Flask, abort, g, jsonify, render_template, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("MUSICBOARD_DATA", BASE_DIR / "instance"))
DB_PATH = DATA_DIR / "musicboard.db"
COVER_DIR = DATA_DIR / "covers"
ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB uploads


# ---------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS artists (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    key       TEXT NOT NULL UNIQUE,
    position  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS albums (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
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


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.executescript(SCHEMA)


# ---------------------------------------------------------------- helpers

def artist_key(name):
    return " ".join((name or "").lower().split())


def clean_rating(value):
    """Return a rating between 1 and 10 in half steps, or None for blank."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        abort(400, "Rating must be a number from 1 to 10.")
    number = min(10.0, max(1.0, number))
    return round(number * 2) / 2


def find_or_create_artist(db, name):
    name = " ".join((name or "").split()) or "Unknown artist"
    key = artist_key(name)
    row = db.execute("SELECT id FROM artists WHERE key = ?", (key,)).fetchone()
    if row:
        return row["id"]
    pos = db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM artists").fetchone()[0]
    cur = db.execute("INSERT INTO artists (name, key, position) VALUES (?, ?, ?)", (name, key, pos))
    return cur.lastrowid


def remove_empty_artists(db):
    db.execute("DELETE FROM artists WHERE id NOT IN (SELECT DISTINCT artist_id FROM albums)")


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
    row = db.execute(
        "SELECT albums.*, artists.name AS artist FROM albums JOIN artists ON artists.id = albums.artist_id "
        "WHERE albums.id = ?",
        (album_id,),
    ).fetchone()
    if row is None:
        abort(404, "Album not found.")
    return row


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(413)
def json_error(err):
    message = getattr(err, "description", "Request failed.")
    if err.code == 413:
        message = "That image is too large. Pick one under 10 MB."
    if request.path.startswith("/api/"):
        return jsonify(error=message), err.code
    return message, err.code


# ---------------------------------------------------------------- pages

@app.context_processor
def asset_helpers():
    def asset(filename):
        """Static file URL that changes whenever the file changes, so browsers never use a stale copy."""
        try:
            version = int((BASE_DIR / "static" / filename).stat().st_mtime)
        except OSError:
            version = 0
        return f"/static/{filename}?v={version}"
    return {"asset": asset}


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/covers/<path:filename>")
def cover(filename):
    return send_from_directory(COVER_DIR, filename)


# ---------------------------------------------------------------- API

@app.get("/api/library")
def library():
    db = get_db()
    artists = [dict(r) for r in db.execute("SELECT id, name, position FROM artists ORDER BY position, id")]
    rows = db.execute(
        "SELECT albums.*, artists.name AS artist FROM albums JOIN artists ON artists.id = albums.artist_id "
        "ORDER BY albums.position, albums.created_at"
    ).fetchall()
    return jsonify(artists=artists, albums=[album_json(db, r) for r in rows])


@app.post("/api/albums")
def create_album():
    db = get_db()
    title = (request.form.get("title") or "").strip()
    artist = (request.form.get("artist") or "").strip()
    if not title or not artist:
        abort(400, "Add both the album name and the artist.")
    cover_name = save_cover(request.files.get("cover"))
    artist_id = find_or_create_artist(db, artist)
    cur = db.execute(
        "INSERT INTO albums (title, artist_id, cover, position, created_at) VALUES (?, ?, ?, ?, ?)",
        (title, artist_id, cover_name, next_album_position(db, artist_id), time.time()),
    )
    album_id = cur.lastrowid
    db.execute("INSERT INTO songs (album_id, name, rating, position) VALUES (?, '', NULL, 0)", (album_id,))
    db.commit()
    return jsonify(album_json(db, get_album_row(db, album_id))), 201


@app.patch("/api/albums/<int:album_id>")
def update_album(album_id):
    db = get_db()
    row = get_album_row(db, album_id)
    data = request.get_json(silent=True) or {}
    if "title" in data:
        title = (data["title"] or "").strip() or "Untitled"
        db.execute("UPDATE albums SET title = ? WHERE id = ?", (title, album_id))
    if "artist" in data:
        new_artist_id = find_or_create_artist(db, data["artist"])
        if new_artist_id != row["artist_id"]:
            db.execute(
                "UPDATE albums SET artist_id = ?, position = ? WHERE id = ?",
                (new_artist_id, next_album_position(db, new_artist_id), album_id),
            )
            remove_empty_artists(db)
    db.commit()
    return jsonify(album_json(db, get_album_row(db, album_id)))


@app.post("/api/albums/<int:album_id>/cover")
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
def delete_album(album_id):
    db = get_db()
    row = get_album_row(db, album_id)
    db.execute("DELETE FROM songs WHERE album_id = ?", (album_id,))
    db.execute("DELETE FROM albums WHERE id = ?", (album_id,))
    remove_empty_artists(db)
    db.commit()
    delete_cover(row["cover"])
    return "", 204


@app.post("/api/albums/<int:album_id>/songs")
def add_song(album_id):
    db = get_db()
    get_album_row(db, album_id)
    data = request.get_json(silent=True) or {}
    pos = db.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM songs WHERE album_id = ?", (album_id,)
    ).fetchone()[0]
    cur = db.execute(
        "INSERT INTO songs (album_id, name, rating, position) VALUES (?, ?, ?, ?)",
        (album_id, (data.get("name") or "").strip(), clean_rating(data.get("rating")), pos),
    )
    db.commit()
    return jsonify(id=cur.lastrowid, name=(data.get("name") or "").strip(), rating=clean_rating(data.get("rating"))), 201


@app.patch("/api/songs/<int:song_id>")
def update_song(song_id):
    db = get_db()
    if db.execute("SELECT 1 FROM songs WHERE id = ?", (song_id,)).fetchone() is None:
        abort(404, "Song not found.")
    data = request.get_json(silent=True) or {}
    if "name" in data:
        db.execute("UPDATE songs SET name = ? WHERE id = ?", ((data["name"] or "").strip(), song_id))
    if "rating" in data:
        db.execute("UPDATE songs SET rating = ? WHERE id = ?", (clean_rating(data["rating"]), song_id))
    db.commit()
    return jsonify(dict(db.execute("SELECT id, name, rating FROM songs WHERE id = ?", (song_id,)).fetchone()))


@app.delete("/api/songs/<int:song_id>")
def delete_song(song_id):
    db = get_db()
    db.execute("DELETE FROM songs WHERE id = ?", (song_id,))
    db.commit()
    return "", 204


@app.put("/api/order")
def save_order():
    """Body: {"artists": [artist ids in order]} and/or {"albums": [album ids in order]}."""
    db = get_db()
    data = request.get_json(silent=True) or {}
    for i, artist_id in enumerate(data.get("artists") or []):
        db.execute("UPDATE artists SET position = ? WHERE id = ?", (i, int(artist_id)))
    for i, album_id in enumerate(data.get("albums") or []):
        db.execute("UPDATE albums SET position = ? WHERE id = ?", (i, int(album_id)))
    db.commit()
    return "", 204


init_db()

if __name__ == "__main__":
    app.run(debug=True)
