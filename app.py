"""A small, local-first URL shortener backed by SQLite."""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlparse

from flask import Flask, abort, flash, g, get_flashed_messages, jsonify, redirect
from flask import render_template, request, url_for

__version__ = "1.2.0"
SHORTNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
RESERVED_NAMES = frozenset(
    {"api", "home", "healthz", "listdb", "manage", "static", "favicon.ico"}
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _normalise_tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",")]
    if not isinstance(tags, (list, tuple)) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError("tags must be a list of strings")
    result: list[str] = []
    for tag in tags:
        tag = tag.strip()
        if tag and tag not in result:
            result.append(tag[:64])
    return result[:32]


class ShortlinkDB:
    """SQLite-backed store. A process lock also protects concurrent writers."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS shortlinks (
        name       TEXT PRIMARY KEY,
        url        TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        tags       TEXT NOT NULL DEFAULT '[]',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """

    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
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
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(shortlinks)")}
            if "description" not in columns:
                try:
                    conn.execute(
                        "ALTER TABLE shortlinks ADD COLUMN description TEXT NOT NULL DEFAULT ''"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            if "tags" not in columns:
                try:
                    conn.execute("ALTER TABLE shortlinks ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        try:
            tags = json.loads(row["tags"] or "[]")
        except (TypeError, ValueError):
            tags = []
        return {
            "name": row["name"],
            "url": row["url"],
            "description": row["description"] or "",
            "tags": tags if isinstance(tags, list) else [],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def count(self, query: str = "") -> int:
        with self._lock, self._connect() as conn:
            if query:
                pattern = f"%{query}%"
                row = conn.execute(
                    "SELECT COUNT(*) FROM shortlinks WHERE name LIKE ? OR url LIKE ? "
                    "OR description LIKE ? OR tags LIKE ?",
                    (pattern, pattern, pattern, pattern),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM shortlinks").fetchone()
            return int(row[0])

    def all(self) -> dict[str, str]:
        return {item["name"]: item["url"] for item in self.list_records()}

    def list_records(self, query: str = "", offset: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            sql = "SELECT name,url,description,tags,created_at,updated_at FROM shortlinks"
            args: list[Any] = []
            if query:
                pattern = f"%{query}%"
                sql += " WHERE name LIKE ? OR url LIKE ? OR description LIKE ? OR tags LIKE ?"
                args.extend([pattern] * 4)
            sql += " ORDER BY name COLLATE NOCASE"
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                args.extend([limit, offset])
            return [self._decode(row) for row in conn.execute(sql, args).fetchall()]

    def all_detailed(self) -> list[tuple[str, str, str, str]]:
        """Return the original detailed tuple shape for callers of older versions."""
        return [
            (item["name"], item["url"], item["created_at"], item["updated_at"])
            for item in self.list_records()
        ]

    def get(self, name: str) -> str | None:
        record = self.get_record(name)
        return record["url"] if record else None

    def get_record(self, name: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT name,url,description,tags,created_at,updated_at FROM shortlinks WHERE name = ?",
                (name,),
            ).fetchone()
            return self._decode(row) if row else None

    def upsert(
        self, name: str, url: str, description: str | None = None, tags: Any = None
    ) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT name,url,description,tags,created_at,updated_at FROM shortlinks WHERE name = ?",
                    (name,),
                ).fetchone()
                if existing:
                    current = self._decode(existing)
                    if description is None:
                        description = current["description"]
                    if tags is None:
                        tags = current["tags"]
                    tags_json = json.dumps(_normalise_tags(tags), ensure_ascii=False)
                    conn.execute(
                        "UPDATE shortlinks SET url=?, description=?, tags=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE name=?",
                        (url, description.strip()[:500], tags_json, name),
                    )
                    conn.execute("COMMIT")
                    return False
                tags_json = json.dumps(_normalise_tags(tags), ensure_ascii=False)
                conn.execute(
                    "INSERT INTO shortlinks(name,url,description,tags) VALUES (?,?,?,?)",
                    (name, url, (description or "").strip()[:500], tags_json),
                )
                conn.execute("COMMIT")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def rename(
        self, old_name: str, new_name: str, url: str, description: str | None = None,
        tags: Any = None
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT name,url,description,tags,created_at,updated_at FROM shortlinks WHERE name=?",
                    (old_name,),
                ).fetchone()
                if not existing:
                    raise KeyError(old_name)
                if new_name != old_name and conn.execute(
                    "SELECT 1 FROM shortlinks WHERE name=?", (new_name,)
                ).fetchone():
                    raise ValueError(f"shortlink {new_name!r} already exists")
                current = self._decode(existing)
                if description is None:
                    description = current["description"]
                if tags is None:
                    tags = current["tags"]
                tags_json = json.dumps(_normalise_tags(tags), ensure_ascii=False)
                conn.execute(
                    "UPDATE shortlinks SET name=?,url=?,description=?,tags=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE name=?",
                    (new_name, url, (description or "").strip()[:500], tags_json, old_name),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def delete(self, name: str) -> bool:
        with self._lock, self._connect() as conn:
            return conn.execute("DELETE FROM shortlinks WHERE name=?", (name,)).rowcount > 0

    def import_records(self, records: list[Mapping[str, Any]]) -> int:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN")
            try:
                imported = 0
                for record in records:
                    if not isinstance(record, Mapping):
                        raise ValueError("each link must be an object")
                    name, url = record.get("name"), record.get("url")
                    if not isinstance(name, str) or not _valid_shortname(name):
                        raise ValueError(f"invalid short name: {name!r}")
                    if not isinstance(url, str) or not _valid_url(url):
                        raise ValueError(f"invalid URL for {name!r}")
                    description = record.get("description", "")
                    if not isinstance(description, str):
                        raise ValueError(f"invalid description for {name!r}")
                    tags = record.get("tags")
                    if tags is not None and not isinstance(tags, list):
                        raise ValueError(f"invalid tags for {name!r}")
                    tags_json = json.dumps(_normalise_tags(tags), ensure_ascii=False)
                    created_at = record.get("created_at")
                    updated_at = record.get("updated_at")
                    if created_at is not None and (
                        not isinstance(created_at, str) or len(created_at) > 64
                    ):
                        raise ValueError(f"invalid created_at for {name!r}")
                    if updated_at is not None and (
                        not isinstance(updated_at, str) or len(updated_at) > 64
                    ):
                        raise ValueError(f"invalid updated_at for {name!r}")
                    existing = conn.execute(
                        "SELECT 1 FROM shortlinks WHERE name=?", (name,)
                    ).fetchone()
                    if existing:
                        conn.execute(
                            "UPDATE shortlinks SET url=?,description=?,tags=?,"
                            "updated_at=COALESCE(?,CURRENT_TIMESTAMP) WHERE name=?",
                            (url, description.strip()[:500], tags_json, updated_at, name),
                        )
                    elif created_at is not None or updated_at is not None:
                        conn.execute(
                            "INSERT INTO shortlinks(name,url,description,tags,created_at,updated_at) "
                            "VALUES (?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP),COALESCE(?,CURRENT_TIMESTAMP))",
                            (name, url, description.strip()[:500], tags_json, created_at, updated_at),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO shortlinks(name,url,description,tags) VALUES (?,?,?,?)",
                            (name, url, description.strip()[:500], tags_json),
                        )
                    imported += 1
                conn.execute("COMMIT")
                return imported
            except Exception:
                conn.execute("ROLLBACK")
                raise


def _valid_shortname(name: str) -> bool:
    return bool(SHORTNAME_RE.fullmatch(name)) and name not in RESERVED_NAMES


def _valid_url(url: str) -> bool:
    if not isinstance(url, str) or not url or any(char.isspace() or ord(char) < 32 for char in url):
        return False
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(hostname)


def create_app(db_path: str | os.PathLike[str] | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "go-shortlink-local-dev")
    app.config["REDIRECT_CODE"] = _redirect_code(os.environ.get("SHORTLINK_REDIRECT_CODE", "302"))
    app.config["SHORTLINK_RATE_LIMIT"] = _env_int("SHORTLINK_RATE_LIMIT", 60)
    app.config["SHORTLINK_RATE_WINDOW"] = _env_int("SHORTLINK_RATE_WINDOW", 60)
    app.config["SHORTLINK_API_TOKEN"] = os.environ.get("SHORTLINK_API_TOKEN", "").strip()
    logging.basicConfig(
        format="[%(asctime)s] %(levelname)s in %(module)s: %(message)s",
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )
    db_resolved = Path(db_path or os.environ.get("SHORTLINK_DB_PATH") or _project_root() / "shortlinks.db")
    db = ShortlinkDB(db_resolved)
    app.config["SHORTLINK_DB"] = db
    app.logger.info("Using shortlink DB %s (%d entries)", db_resolved, db.count())
    request_times: dict[str, deque[float]] = {}
    rate_lock = threading.Lock()

    @app.before_request
    def request_logging() -> None:
        g.request_started = time.monotonic()
        app.logger.info("%s %s from %s", request.method, request.path, request.remote_addr or "-")

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; img-src 'self' data:",
        )
        started = getattr(g, "request_started", None)
        if started is not None:
            app.logger.info("completed %s %s (%s) in %.1fms", request.method, request.path,
                            response.status_code, (time.monotonic() - started) * 1000)
        return response

    def api_error(message: str, status: int):
        return jsonify(error=message, status=status), status

    def require_api_auth():
        token = app.config.get("SHORTLINK_API_TOKEN", "")
        supplied = request.headers.get("Authorization", "")
        if supplied.startswith("Bearer "):
            supplied = supplied.removeprefix("Bearer ")
        else:
            supplied = request.headers.get("X-API-Key", "")
        if token and supplied != token:
            response = api_error("authentication required", 401)
            response[0].headers["WWW-Authenticate"] = "Bearer"
            return response
        return None

    def rate_limit(write_api: bool = False):
        limit = int(app.config.get("SHORTLINK_RATE_LIMIT", 0))
        if limit <= 0:
            return None
        key = request.remote_addr or "local"
        now = time.monotonic()
        with rate_lock:
            bucket = request_times.setdefault(key, deque())
            window = max(1, int(app.config.get("SHORTLINK_RATE_WINDOW", 60)))
            while bucket and bucket[0] <= now - window:
                bucket.popleft()
            if len(bucket) >= limit:
                return api_error("rate limit exceeded", 429) if write_api else ("Too many requests", 429)
            bucket.append(now)
        return None

    def parse_json():
        if not request.is_json:
            return None, api_error("request body must be JSON", 400)
        try:
            value = request.get_json(silent=False)
        except Exception:
            return None, api_error("malformed JSON body", 400)
        if not isinstance(value, dict):
            return None, api_error("JSON body must be an object", 400)
        return value, None

    # UI
    @app.route("/home")
    def home():
        return render_template("index.html")

    @app.route("/listdb")
    def listdb():
        query = (request.args.get("q") or "").strip()[:100]
        page = max(1, _env_int_value(request.args.get("page"), 1))
        per_page = min(100, max(1, _env_int_value(request.args.get("per_page"), 25)))
        total = db.count(query)
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        entries = db.list_records(query, (page - 1) * per_page, per_page)
        return render_template("list.html", entries=entries, query=query, page=page,
                               per_page=per_page, total=total, pages=pages)

    @app.route("/manage")
    def manage():
        return render_template(
            "manage.html",
            entries=db.list_records(),
            messages=get_flashed_messages(with_categories=True),
        )

    @app.route("/manage/add", methods=["POST"])
    def manage_add():
        limited = rate_limit()
        if limited:
            return limited
        name = (request.form.get("name") or "").strip()
        url = (request.form.get("url") or "").strip()
        description = request.form.get("description") or ""
        tags = request.form.get("tags") or ""
        if not _valid_shortname(name):
            flash(f"Invalid short name: {name!r}", "error")
        elif not _valid_url(url):
            flash("URL must start with http:// or https://", "error")
        elif db.get(name) is not None:
            flash(f"Shortlink {name!r} already exists; use Save to update it.", "error")
        else:
            db.upsert(name, url, description, tags)
            flash(f"Added {name} → {url}", "success")
        return redirect(url_for("manage"))

    @app.route("/manage/edit/<name>", methods=["POST"])
    def manage_edit(name: str):
        limited = rate_limit()
        if limited:
            return limited
        new_name = (request.form.get("new_name") or name).strip()
        new_url = (request.form.get("url") or "").strip()
        description = request.form.get("description") or ""
        tags = request.form.get("tags") or ""
        if not _valid_shortname(new_name):
            flash(f"Invalid short name: {new_name!r}", "error")
        elif not _valid_url(new_url):
            flash("URL must start with http:// or https://", "error")
        else:
            try:
                db.rename(name, new_name, new_url, description, tags)
            except KeyError:
                flash(f"Shortlink {name!r} no longer exists.", "error")
            except ValueError as err:
                flash(str(err), "error")
            else:
                flash(f"Updated {new_name} → {new_url}", "success")
        return redirect(url_for("manage"))

    @app.route("/manage/delete/<name>", methods=["POST"])
    def manage_delete(name: str):
        limited = rate_limit()
        if limited:
            return limited
        if db.delete(name):
            flash(f"Deleted {name}", "success")
        else:
            flash(f"Shortlink {name!r} not found.", "error")
        return redirect(url_for("manage"))

    # API
    @app.route("/healthz")
    def healthz():
        return jsonify(status="ok", entries=db.count(), version=__version__)

    @app.route("/api/urls", methods=["GET"])
    def api_list():
        query = (request.args.get("q") or "").strip()[:100]
        detailed = (
            request.args.get("details") in {"1", "true", "yes"}
            or bool(query)
            or "page" in request.args
            or "per_page" in request.args
        )
        if not detailed:
            return jsonify(db.all())
        page = max(1, _env_int_value(request.args.get("page"), 1))
        per_page = min(100, max(1, _env_int_value(request.args.get("per_page"), 25)))
        total = db.count(query)
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        return jsonify(items=db.list_records(query, (page - 1) * per_page, per_page),
                       pagination={"page": page, "per_page": per_page, "total": total,
                                   "pages": pages})

    @app.route("/api/urls/export")
    @app.route("/api/export")
    def api_export():
        records = db.list_records()
        return jsonify(version=1, exported_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       links=records)

    @app.route("/api/urls/import", methods=["POST"])
    @app.route("/api/import", methods=["POST"])
    def api_import():
        auth = require_api_auth()
        if auth:
            return auth
        limited = rate_limit(write_api=True)
        if limited:
            return limited
        payload, error = parse_json()
        if error:
            return error
        raw_records = payload.get("links")
        if not isinstance(raw_records, list):
            return api_error("'links' must be a list", 400)
        try:
            imported = db.import_records(raw_records)
        except (ValueError, TypeError) as exc:
            return api_error(str(exc), 400)
        return jsonify(imported=imported), 200

    @app.route("/api/urls/<name>", methods=["GET"])
    def api_get(name: str):
        record = db.get_record(name)
        if record is None:
            return api_error("shortlink not found", 404)
        return jsonify(record)

    @app.route("/api/urls/<name>", methods=["PUT"])
    def api_put(name: str):
        auth = require_api_auth()
        if auth:
            return auth
        limited = rate_limit(write_api=True)
        if limited:
            return limited
        if not _valid_shortname(name):
            return api_error("invalid short name", 400)
        payload, error = parse_json()
        if error:
            return error
        url = payload.get("url")
        if not isinstance(url, str) or not _valid_url(url):
            return api_error("url must be a valid http(s) URL", 400)
        try:
            description = payload.get("description")
            tags = payload.get("tags")
            if description is not None and not isinstance(description, str):
                raise ValueError("description must be a string")
            if tags is not None and not isinstance(tags, list):
                raise ValueError("tags must be a list of strings")
            created = db.upsert(name, url, description, tags)
        except (ValueError, TypeError) as exc:
            return api_error(str(exc), 400)
        return jsonify(db.get_record(name)), (201 if created else 200)

    @app.route("/api/urls/<name>", methods=["DELETE"])
    def api_delete(name: str):
        auth = require_api_auth()
        if auth:
            return auth
        limited = rate_limit(write_api=True)
        if limited:
            return limited
        if not db.delete(name):
            return api_error("shortlink not found", 404)
        return ("", 204)

    @app.route("/")
    def root():
        return redirect(url_for("home"), code=302)

    @app.route("/<name>")
    def go(name: str):
        if name in RESERVED_NAMES:
            abort(404)
        url = db.get(name)
        if url is None:
            app.logger.info("shortlink miss: %r", name)
            return render_template("404.html", name=name), 404
        app.logger.info("shortlink hit: %r -> %s", name, url)
        return redirect(url, code=app.config["REDIRECT_CODE"])

    @app.errorhandler(404)
    def not_found(_err):
        if request.path == "/api" or request.path.startswith("/api/"):
            return api_error("not found", 404)
        return render_template("404.html", name=request.path.lstrip("/")), 404

    @app.errorhandler(400)
    def bad_request(_err):
        if request.path == "/api" or request.path.startswith("/api/"):
            return api_error("bad request", 400)
        return "Bad request", 400

    @app.errorhandler(405)
    def method_not_allowed(_err):
        if request.path == "/api" or request.path.startswith("/api/"):
            return api_error("method not allowed", 405)
        return "Method not allowed", 405

    @app.errorhandler(500)
    def internal_error(_err):
        if request.path == "/api" or request.path.startswith("/api/"):
            return api_error("internal server error", 500)
        return "Internal server error", 500

    return app


def _env_int(name: str, default: int) -> int:
    return _env_int_value(os.environ.get(name), default)


def _env_int_value(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _redirect_code(value: str) -> int:
    try:
        code = int(value)
    except (TypeError, ValueError):
        return 302
    return code if code in {301, 302, 307, 308} else 302


app = create_app()

if __name__ == "__main__":
    host = os.environ.get("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_RUN_PORT", "8080"))
    debug = os.environ.get("FLASK_DEBUG", "0").lower() in {"1", "true", "yes"}
    app.run(host=host, port=port, debug=debug)
