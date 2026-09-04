import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import ShortlinkDB, create_app


@pytest.fixture
def db_file(tmp_path):
    return tmp_path / "shortlinks.db"


@pytest.fixture
def client(db_file):
    app = create_app(db_path=db_file)
    app.config.update(TESTING=True)
    app.config["SHORTLINK_DB"].upsert("gh", "https://github.com/")
    with app.test_client() as c:
        yield c


def test_root_redirects_to_home(client):
    rsp = client.get("/")
    assert rsp.status_code == 302
    assert rsp.headers["Location"].endswith("/home")


def test_known_shortlink_redirects(client):
    rsp = client.get("/gh")
    assert rsp.status_code == 302
    assert rsp.headers["Location"] == "https://github.com/"


def test_unknown_shortlink_returns_404(client):
    rsp = client.get("/nope")
    assert rsp.status_code == 404


def test_healthz(client):
    rsp = client.get("/healthz")
    assert rsp.status_code == 200
    body = rsp.get_json()
    assert body["status"] == "ok"
    assert body["entries"] == 1


def test_api_list(client):
    rsp = client.get("/api/urls")
    assert rsp.status_code == 200
    assert rsp.get_json() == {"gh": "https://github.com/"}


def test_api_put_creates_entry(client):
    rsp = client.put("/api/urls/yt", json={"url": "https://youtube.com/"})
    assert rsp.status_code == 201
    assert client.get("/yt").headers["Location"] == "https://youtube.com/"


def test_api_put_updates_entry(client):
    rsp = client.put("/api/urls/gh", json={"url": "https://github.com/other"})
    assert rsp.status_code == 200
    assert client.get("/gh").headers["Location"] == "https://github.com/other"


def test_api_put_rejects_invalid_url(client):
    rsp = client.put("/api/urls/bad", json={"url": "javascript:alert(1)"})
    assert rsp.status_code == 400


def test_api_put_rejects_reserved_name(client):
    rsp = client.put("/api/urls/api", json={"url": "https://example.com/"})
    assert rsp.status_code == 400


def test_api_put_rejects_invalid_shortname(client):
    rsp = client.put("/api/urls/has space", json={"url": "https://example.com/"})
    assert rsp.status_code == 400


def test_shortname_boundaries(client):
    assert client.put("/api/urls/" + "a" * 64, json={"url": "https://example.com"}).status_code == 201
    assert client.put("/api/urls/" + "a" * 65, json={"url": "https://example.com"}).status_code == 400
    assert client.put("/api/urls/favicon.ico", json={"url": "https://example.com"}).status_code == 400


def test_api_method_errors_are_json(client):
    rsp = client.post("/api/urls/gh")
    assert rsp.status_code == 405
    assert rsp.get_json()["error"] == "method not allowed"


def test_api_delete(client):
    rsp = client.delete("/api/urls/gh")
    assert rsp.status_code == 204
    assert client.get("/gh").status_code == 404


def test_api_delete_missing(client):
    rsp = client.delete("/api/urls/nope")
    assert rsp.status_code == 404


# -------- UI form endpoints --------

def test_manage_page_renders(client):
    rsp = client.get("/manage")
    assert rsp.status_code == 200
    assert b"Shortlinks" in rsp.data
    assert b"gh" in rsp.data


def test_manage_add(client):
    rsp = client.post("/manage/add", data={"name": "yt", "url": "https://youtube.com/"})
    assert rsp.status_code == 302
    assert client.get("/yt").headers["Location"] == "https://youtube.com/"


def test_manage_add_rejects_duplicate(client):
    rsp = client.post("/manage/add", data={"name": "gh", "url": "https://example.com/"},
                      follow_redirects=True)
    assert b"already exists" in rsp.data
    # original URL still in place
    assert client.get("/gh").headers["Location"] == "https://github.com/"


def test_manage_add_rejects_bad_url(client):
    rsp = client.post("/manage/add", data={"name": "bad", "url": "ftp://x"},
                      follow_redirects=True)
    assert b"http" in rsp.data
    assert client.get("/bad").status_code == 404


def test_manage_edit_updates_url(client):
    rsp = client.post("/manage/edit/gh",
                      data={"new_name": "gh", "url": "https://github.com/anthropics"})
    assert rsp.status_code == 302
    assert client.get("/gh").headers["Location"] == "https://github.com/anthropics"


def test_manage_edit_renames(client):
    rsp = client.post("/manage/edit/gh",
                      data={"new_name": "github", "url": "https://github.com/"})
    assert rsp.status_code == 302
    assert client.get("/gh").status_code == 404
    assert client.get("/github").headers["Location"] == "https://github.com/"


def test_manage_edit_rename_collision(client):
    client.post("/manage/add", data={"name": "yt", "url": "https://youtube.com/"})
    rsp = client.post("/manage/edit/gh",
                      data={"new_name": "yt", "url": "https://github.com/"},
                      follow_redirects=True)
    assert b"already exists" in rsp.data
    assert client.get("/gh").status_code == 302  # unchanged
    assert client.get("/yt").headers["Location"] == "https://youtube.com/"


def test_manage_delete(client):
    rsp = client.post("/manage/delete/gh")
    assert rsp.status_code == 302
    assert client.get("/gh").status_code == 404


def test_api_metadata_and_escaping(client):
    rsp = client.put(
        "/api/urls/meta",
        json={
            "url": "https://example.com/",
            "description": "<script>alert(1)</script>",
            "tags": ["one", "two"],
        },
    )
    assert rsp.status_code == 201
    assert rsp.get_json()["tags"] == ["one", "two"]
    page = client.get("/listdb")
    assert b"&lt;script&gt;" in page.data
    assert b"<script>alert(1)</script>" not in page.data


def test_api_malformed_json_is_consistent(client):
    rsp = client.put(
        "/api/urls/bad-json",
        data='{"url":',
        content_type="application/json",
    )
    assert rsp.status_code == 400
    assert rsp.get_json()["error"] == "malformed JSON body"


def test_security_headers_are_set(client):
    rsp = client.get("/healthz")
    assert rsp.headers["X-Content-Type-Options"] == "nosniff"
    assert rsp.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in rsp.headers


def test_redirect_code_is_configurable(client):
    client.application.config["REDIRECT_CODE"] = 307
    assert client.get("/gh").status_code == 307


def test_server_side_filter_and_pagination(client):
    db = client.application.config["SHORTLINK_DB"]
    for name in ("alpha", "beta", "gamma"):
        db.upsert(name, f"https://example.com/{name}", description="needle" if name == "beta" else "")
    rsp = client.get("/listdb?q=needle&per_page=1")
    assert rsp.status_code == 200
    assert b"beta" in rsp.data
    assert b"alpha" not in rsp.data
    assert b"Page" not in rsp.data


def test_export_import_round_trip(db_file):
    app = create_app(db_path=db_file)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        c.put("/api/urls/exported", json={"url": "https://example.com", "tags": ["x"]})
        exported = c.get("/api/urls/export").get_json()
    other = create_app(db_path=db_file.with_name("other.db"))
    other.config.update(TESTING=True)
    with other.test_client() as c:
        rsp = c.post("/api/urls/import", json={"links": exported["links"]})
        assert rsp.status_code == 200
        assert c.get("/api/urls/exported").get_json()["tags"] == ["x"]


def test_existing_schema_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE shortlinks (name TEXT PRIMARY KEY, url TEXT NOT NULL, "
                     "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                     "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("INSERT INTO shortlinks(name,url) VALUES ('old','https://example.com')")
    db = ShortlinkDB(path)
    record = db.get_record("old")
    assert record["description"] == ""
    assert record["tags"] == []


def test_concurrent_upserts_are_safe(db_file):
    db = ShortlinkDB(db_file)
    def write(index):
        return db.upsert(f"n{index}", f"https://example.com/{index}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(30)))
    assert db.count() == 30
