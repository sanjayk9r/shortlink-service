"""go-shortlink: a tiny local URL-shortener service.

Browse to ``http://go/<name>`` to be redirected to the URL registered for
``<name>`` in the shortlink database (a SQLite file).
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Tuple
from urllib.parse import urlparse

from flask import (
    Flask,
    abort,
    flash,
    get_flashed_messages,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

__version__ = "1.1.0"

# Short-name format: letters, digits, dash, underscore, dot. Keeps the URL
# space predictable and avoids collisions with the API/health/UI routes.
SHORTNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

# Routes that are part of the app surface and therefore cannot be used as
# short-link names.
RESERVED_NAMES = frozenset(
    {"api", "home", "healthz", "listdb", "manage", "static", "favicon.ico"}
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent


class ShortlinkDB:
    """SQLite-backed store for shortlinks. Thread-safe via a connection lock."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS shortlinks (
        name       TEXT PRIMARY KEY,
        url        TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """

    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(self.SCHEMA)

    def count(self) -> int:
        with self._lock, self._connect() as conn:
            (n,) = conn.execute("SELECT COUNT(*) FROM shortlinks").fetchone()
            return int(n)

    def all(self) -> Dict[str, str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT name, url FROM shortlinks ORDER BY name").fetchall()
            return {r["name"]: r["url"] for r in rows}

    def all_detailed(self) -> List[Tuple[str, str, str, str]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT name, url, created_at, updated_at FROM shortlinks ORDER BY name"
            ).fetchall()
            return [(r["name"], r["url"], r["created_at"], r["updated_at"]) for r in rows]

    def get(self, name: str) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT url FROM shortlinks WHERE name = ?", (name,)).fetchone()
            return row["url"] if row else None

    def upsert(self, name: str, url: str) -> bool:
        """Insert or update. Returns True if a new entry was created."""
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM shortlinks WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE shortlinks SET url = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                    (url, name),
                )
                return False
            conn.execute(
                "INSERT INTO shortlinks(name, url) VALUES (?, ?)",
                (name, url),
            )
            return True

    def rename(self, old_name: str, new_name: str, url: str) -> None:
        """Rename an entry (and optionally update its URL) atomically.

        Raises KeyError if ``old_name`` doesn't exist, ValueError if
        ``new_name`` already exists and differs from ``old_name``.
        """
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM shortlinks WHERE name = ?", (old_name,)
            ).fetchone()
            if not row:
                raise KeyError(old_name)
            if new_name != old_name:
                clash = conn.execute(
                    "SELECT 1 FROM shortlinks WHERE name = ?", (new_name,)
                ).fetchone()
                if clash:
                    raise ValueError(f"shortlink {new_name!r} already exists")
            conn.execute(
                "UPDATE shortlinks SET name = ?, url = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE name = ?",
                (new_name, url, old_name),
            )

    def delete(self, name: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM shortlinks WHERE name = ?", (name,))
            return cur.rowcount > 0


def _valid_shortname(name: str) -> bool:
    return bool(SHORTNAME_RE.match(name)) and name not in RESERVED_NAMES


def _valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def create_app(db_path: str | os.PathLike[str] | None = None) -> Flask:
    """Application factory.

    ``db_path`` overrides ``SHORTLINK_DB_PATH`` (default: ``./shortlinks.db``).
    """
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "go-shortlink-local-dev")

    logging.basicConfig(
        format="[%(asctime)s] %(levelname)s in %(module)s: %(message)s",
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )

    db_resolved = Path(
        db_path
        or os.environ.get("SHORTLINK_DB_PATH")
        or _project_root() / "shortlinks.db"
    )
    db = ShortlinkDB(db_resolved)
    app.config["SHORTLINK_DB"] = db
    app.logger.info("Using shortlink DB %s (%d entries)", db_resolved, db.count())

    # -------- UI routes --------

    @app.route("/home", methods=["GET"])
    def home():
        return render_template("index.html")

    @app.route("/listdb", methods=["GET"])
    def listdb():
        return render_template("list.html", my_listdb=db.all())

    @app.route("/manage", methods=["GET"])
    def manage():
        return render_template(
            "manage.html",
            entries=db.all_detailed(),
            messages=get_flashed_messages(with_categories=True),
        )

    @app.route("/manage/add", methods=["POST"])
    def manage_add():
        name = (request.form.get("name") or "").strip()
        url = (request.form.get("url") or "").strip()
        if not _valid_shortname(name):
            flash(f"Invalid short name: {name!r}", "error")
        elif not _valid_url(url):
            flash("URL must start with http:// or https://", "error")
        elif db.get(name) is not None:
            flash(f"Shortlink {name!r} already exists; use Save to update it.", "error")
        else:
            db.upsert(name, url)
            flash(f"Added {name} → {url}", "success")
        return redirect(url_for("manage"))

    @app.route("/manage/edit/<name>", methods=["POST"])
    def manage_edit(name: str):
        new_name = (request.form.get("new_name") or name).strip()
        new_url = (request.form.get("url") or "").strip()
        if not _valid_shortname(new_name):
            flash(f"Invalid short name: {new_name!r}", "error")
            return redirect(url_for("manage"))
        if not _valid_url(new_url):
            flash("URL must start with http:// or https://", "error")
            return redirect(url_for("manage"))
        try:
            db.rename(name, new_name, new_url)
        except KeyError:
            flash(f"Shortlink {name!r} no longer exists.", "error")
        except ValueError as err:
            flash(str(err), "error")
        else:
            flash(f"Updated {new_name} → {new_url}", "success")
        return redirect(url_for("manage"))

    @app.route("/manage/delete/<name>", methods=["POST"])
    def manage_delete(name: str):
        if db.delete(name):
            flash(f"Deleted {name}", "success")
        else:
            flash(f"Shortlink {name!r} not found.", "error")
        return redirect(url_for("manage"))

    # -------- Health / API --------

    @app.route("/healthz", methods=["GET"])
    def healthz():
        return jsonify(status="ok", entries=db.count(), version=__version__)

    @app.route("/api/urls", methods=["GET"])
    def api_list():
        return jsonify(db.all())

    @app.route("/api/urls/<name>", methods=["GET"])
    def api_get(name: str):
        url = db.get(name)
        if url is None:
            abort(404)
        return jsonify(name=name, url=url)

    @app.route("/api/urls/<name>", methods=["PUT"])
    def api_put(name: str):
        if not _valid_shortname(name):
            return jsonify(error="invalid short name"), 400
        payload = request.get_json(silent=True) or {}
        url = payload.get("url")
        if not isinstance(url, str) or not _valid_url(url):
            return jsonify(error="body must be JSON with a valid http(s) 'url'"), 400
        created = db.upsert(name, url)
        return jsonify(name=name, url=url), (201 if created else 200)

    @app.route("/api/urls/<name>", methods=["DELETE"])
    def api_delete(name: str):
        if not db.delete(name):
            abort(404)
        return ("", 204)

    # -------- Redirect / fallback --------

    @app.route("/", methods=["GET"])
    def root():
        return redirect(url_for("home"), code=302)

    @app.route("/<name>", methods=["GET"])
    def go(name: str):
        if name in RESERVED_NAMES:
            abort(404)
        url = db.get(name)
        if url is None:
            app.logger.info("shortlink miss: %r", name)
            return render_template("404.html", name=name), 404
        app.logger.info("shortlink hit: %r -> %s", name, url)
        return redirect(url, code=302)

    @app.errorhandler(404)
    def not_found(_err):
        return render_template("404.html", name=request.path.lstrip("/")), 404

    return app


app = create_app()


if __name__ == "__main__":
    host = os.environ.get("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_RUN_PORT", "8080"))
    debug = os.environ.get("FLASK_DEBUG", "0") in {"1", "true", "True"}
    app.run(host=host, port=port, debug=debug)
