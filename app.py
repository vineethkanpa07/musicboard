"""Musicboard: rate every song on the albums you listen to, 0.5 to 10.

Everyone signs up with a username and password. Albums are private by default;
switching a page to public makes /<username> show a read-only copy of that
person's dashboard.

Run with:  python app.py   then open http://127.0.0.1:5000
Hosting: see README.md (Render + a free Neon Postgres database).
"""

import math
import os
import re
import secrets
import time
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, g, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import db as database

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("MUSICBOARD_DATA", BASE_DIR / "instance"))
DB_PATH = DATA_DIR / "musicboard.db"
COVER_DIR = DATA_DIR / "covers"

# Album covers are Apple's own artwork links, so nothing needs to be stored for them.
ARTWORK_URL_RE = re.compile(r"^https://[a-z0-9-]+\.mzstatic\.com/[^\s\"'<>]+$")
HOSTED = bool(os.environ.get("RENDER") or os.environ.get("MUSICBOARD_HOSTED"))

USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_.-]{1,28})[a-z0-9]$")
FILE_LIKE_RE = re.compile(r"\.(ico|txt|xml|json|webmanifest|html?|js|css|png|jpe?g|gif|svg|webp|php)$")
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
    MAX_CONTENT_LENGTH=1024 * 1024,  # requests are small JSON now
    SECRET_KEY=load_secret_key(),
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=HOSTED,  # hosted sites use https, so only send the login cookie over https
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,  # stay logged in for 30 days
)
if HOSTED:
    # Render sits in front of the app; trust its headers so the app knows requests arrived over https.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


# ---------------------------------------------------------------- database

def get_db():
    if "db" not in g:
        g.db = database.connect(DB_PATH)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        database.close(conn)


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
    if "/" in name or "\\" in name:
        return "Usernames can't have a slash (/) in them, since your username becomes your page's web address."
    if not USERNAME_RE.match(name):
        return "Usernames are 3 to 30 characters: lowercase letters, numbers, dots, dashes or underscores, starting and ending with a letter or number."
    if name in RESERVED_USERNAMES:
        return "That username is reserved. Pick another one."
    ending = FILE_LIKE_RE.search(name)
    if ending:
        # Browsers and search engines request addresses like /favicon.ico and /robots.txt on every site.
        return (f'Usernames can\'t end in "{ending.group(0)}", because that looks like a file name and could clash '
                "with files every website has. Try it without the ending.")
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


def cover_url(cover):
    """Albums store Apple's artwork link; albums added before hosting support may have a local file."""
    if not cover:
        return None
    return cover if cover.startswith("https://") else f"/covers/{cover}"


def delete_cover(filename):
    if filename and not filename.startswith("https://"):
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
        "cover_url": cover_url(row["cover"]),
        "position": row["position"],
        "created_at": row["created_at"],
        "songs": [dict(s) for s in songs],
        "top_song_id": row["top_song_id"],
        "itunes_id": row["itunes_id"],
        "year": row["year"],
        "rated_at": row["rated_at"],
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
@app.errorhandler(409)
@app.errorhandler(413)
def json_error(err):
    message = getattr(err, "description", "Request failed.")
    if err.code == 413:
        message = "That request was too large."
    if request.path.startswith("/api/"):
        return jsonify(error=message), err.code
    if err.code == 404:
        return render_template("not_found.html"), 404
    return message, err.code


@app.errorhandler(database.DatabaseUnavailable)
def database_unavailable(err):
    database.log(f"Database unavailable: {err}")
    message = "Musicboard can't reach its database right now. Try again in a minute."
    if request.path.startswith("/api/"):
        return jsonify(error=message), 503
    return render_template("not_found.html", heading="Can't reach the database", message=message), 503


@app.context_processor
def template_helpers():
    def asset(filename):
        """Static file URL that changes whenever the file changes, so browsers never use a stale copy."""
        try:
            version = int((BASE_DIR / "static" / filename).stat().st_mtime)
        except OSError:
            version = 0
        return f"/static/{filename}?v={version}"
    try:
        me = current_user()
    except database.DatabaseUnavailable:
        me = None  # still show the page (e.g. the "can't reach the database" message)
    return {"asset": asset, "me": me}


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


# ---------------------------------------------------------------- adding albums from iTunes

def big_artwork(url, size=600):
    """iTunes artwork URLs end in e.g. 100x100bb.jpg; ask for a larger square instead."""
    return re.sub(r"/\d+x\d+bb\.(jpg|png)$", f"/{size}x{size}bb.jpg", url or "") or None


def clean_text(value, limit=300):
    return " ".join(str(value or "").split())[:limit]


def clean_year(value):
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1900 <= year <= time.gmtime().tm_year + 1 else None


@app.post("/api/albums/<int:album_id>/year")
@login_required
def fill_in_year(album_id):
    """Body: {"year": 2016}. Fills in the release year for an album added before years were saved.
    It only sets a missing year, so album details stay as Apple gave them."""
    db = get_db()
    row = get_album_row(db, album_id)
    year = clean_year((request.get_json(silent=True) or {}).get("year"))
    if year is None:
        abort(400, "That isn't a release year.")
    if row["year"] is None:
        db.execute("UPDATE albums SET year = ? WHERE id = ?", (year, album_id))
        db.commit()
    return jsonify(year=row["year"] or year)


def release_order_position(db, artist_id, released):
    """Where a new album goes in its artist's row: before the first album released after it, so the
    row stays oldest-to-newest. Albums with no known date are skipped over. You can still drag
    albums anywhere afterwards; this only picks the starting spot."""
    end = next_album_position(db, artist_id)
    if not released:
        return end
    rows = db.execute(
        "SELECT id, position, released, year FROM albums WHERE artist_id = ? ORDER BY position, created_at", (artist_id,)
    ).fetchall()
    for row in rows:
        other = row["released"] or (str(row["year"]) if row["year"] else None)
        if not other:
            continue
        # compare full dates when both have one; otherwise just the years
        later = other > released if len(other) == len(released) == 10 else other[:4] > released[:4]
        if later:
            # make room: everything from this spot on moves one place to the right
            db.execute("UPDATE albums SET position = position + 1 WHERE artist_id = ? AND position >= ?",
                       (artist_id, row["position"]))
            return row["position"]
    return end


def already_on_shelf(db, itunes_id, title, artist):
    """The same Apple album, or (for albums added before IDs were saved) the same title by the same artist."""
    if db.execute("SELECT 1 FROM albums WHERE user_id = ? AND itunes_id = ?", (uid(), itunes_id)).fetchone():
        return True
    return db.execute(
        "SELECT 1 FROM albums JOIN artists ON artists.id = albums.artist_id "
        "WHERE albums.user_id = ? AND albums.itunes_id IS NULL AND LOWER(albums.title) = LOWER(?) AND artists.key = ?",
        (uid(), title, artist_key(artist)),
    ).fetchone() is not None


@app.post("/api/albums/import")
@login_required
def import_album():
    """Adds an album picked from iTunes search, with its full tracklist.

    The search runs in the visitor's browser (so Apple's rate limit is per visitor, not shared by
    the whole site), and the browser sends the album it looked up:
    {"id": <Apple's album ID>, "title": ..., "artist": ..., "year": 2016, "released": "2016-08-20",
     "artwork": "https://...mzstatic.com/...", "tracks": ["Song", ...]}
    """
    data = request.get_json(silent=True) or {}
    title, artist = clean_text(data.get("title")), clean_text(data.get("artist"))
    try:
        itunes_id = int(data.get("id"))
    except (TypeError, ValueError):
        abort(400, "Pick an album from the search results.")
    tracks = data.get("tracks")
    if not title or not artist or not isinstance(tracks, list) or len(tracks) > 300:
        abort(400, "Pick an album from the search results.")
    names = [clean_text(t) for t in tracks if isinstance(t, str)] or [""]
    year = clean_year(data.get("year"))
    released = str(data.get("released") or "")[:10]
    released = released if re.match(r"^\d{4}-\d{2}-\d{2}$", released) else None
    artwork = big_artwork(data.get("artwork"))
    if artwork and not ARTWORK_URL_RE.match(artwork):
        artwork = None  # only Apple's artwork links are used as covers
    db = get_db()
    if already_on_shelf(db, itunes_id, title, artist):
        abort(409, f"{title} is already on your shelf.")
    artist_id = find_or_create_artist(db, uid(), artist)
    position = release_order_position(db, artist_id, released or (str(year) if year else None))
    cur = db.execute(
        "INSERT INTO albums (user_id, title, artist_id, cover, position, created_at, itunes_id, year, released) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (uid(), title, artist_id, artwork, position, time.time(), itunes_id, year, released),
    )
    album_id = cur.lastrowid
    db.executemany(
        "INSERT INTO songs (album_id, name, rating, position) VALUES (?, ?, NULL, ?)",
        [(album_id, name, i) for i, name in enumerate(names)],
    )
    db.commit()
    return jsonify(album_json(db, get_album_row(db, album_id))), 201


@app.put("/api/albums/<int:album_id>/top")
@login_required
def set_top_track(album_id):
    """Body: {"song_id": id}. Picks the top track among songs tied for the album's highest score."""
    db = get_db()
    get_album_row(db, album_id)
    try:
        song_id = int((request.get_json(silent=True) or {}).get("song_id"))
    except (TypeError, ValueError):
        abort(400, "Pick one of the songs.")
    best = db.execute("SELECT MAX(rating) FROM songs WHERE album_id = ?", (album_id,)).fetchone()[0]
    song = db.execute("SELECT rating FROM songs WHERE id = ? AND album_id = ?", (song_id, album_id)).fetchone()
    if song is None or best is None or song["rating"] != best:
        abort(400, "Only songs with the album's highest score can be the top track.")
    db.execute("UPDATE albums SET top_song_id = ? WHERE id = ?", (song_id, album_id))
    db.commit()
    return jsonify(top_song_id=song_id)


@app.patch("/api/songs/<int:song_id>")
@login_required
def update_song(song_id):
    db = get_db()
    owned_song(db, song_id)
    data = request.get_json(silent=True) or {}  # only the score can change; song names come from iTunes
    if "rating" in data:
        rating = clean_rating(data["rating"])
        song = db.execute("SELECT album_id, rating FROM songs WHERE id = ?", (song_id,)).fetchone()
        if song["rating"] != rating:
            db.execute("UPDATE songs SET rating = ? WHERE id = ?", (rating, song_id))
            # remember when this album was last rated: the newest-rated album wins ties in Top albums
            db.execute("UPDATE albums SET rated_at = ? WHERE id = ?", (time.time(), song["album_id"]))
    db.commit()
    return jsonify(dict(db.execute("SELECT id, name, rating FROM songs WHERE id = ?", (song_id,)).fetchone()))


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


database.init(DB_PATH)

if __name__ == "__main__":
    app.run(debug=os.environ.get("MUSICBOARD_DEBUG", "1") == "1")
