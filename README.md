# shortlink-service (go/shortcut)

A tiny, self-hosted URL shortener for personal use. Point your browser at
`http://go/<name>` and get redirected to whatever URL `<name>` resolves to.
Inspired by the `go/` short-links many companies run internally.

```
http://go/github   ->  https://github.com/
http://go/mail     ->  https://mail.google.com/
http://go/manage   ->  Web UI to add / edit / delete shortlinks
http://go/listdb   ->  Read-only HTML table of every registered shortlink
```


## Quick start

### Option A &mdash; Docker (recommended)

```sh
./build.sh          # or: make docker-build
./run.sh            # or: make docker-run
./stop.sh           # or: make docker-stop
```

`run.sh` creates a Docker volume (`go-shortlink-data`) for the SQLite database
so entries survive container restarts and rebuilds.

### Make `http://go/...` resolve

Add an alias for `go` to your hosts file:

```sh
# /etc/hosts
127.0.0.1   localhost   go
```

Once the container is bound to host port 80, `http://go/<name>` will hit the
service. On macOS / Linux, Docker may require `sudo ./run.sh`; rootless
Podman should use an unprivileged host port such as `HOST_PORT=8080`.

## Configuration

Configuration is supplied with environment variables (the defaults preserve
the original local behaviour):

| Variable | Default | Purpose |
| --- | --- | --- |
| `SHORTLINK_DB_PATH` | `./shortlinks.db` | SQLite database location |
| `SHORTLINK_REDIRECT_CODE` | `302` | Redirect status (`301`, `302`, `307`, or `308`) |
| `SHORTLINK_API_TOKEN` | unset | Optional token for API writes (`Authorization: Bearer ...` or `X-API-Key`) |
| `SHORTLINK_RATE_LIMIT` | `60` | Write requests per client per window; `0` disables |
| `SHORTLINK_RATE_WINDOW` | `60` | Rate-limit window in seconds |
| `FLASK_SECRET_KEY` | local development value | Session/flash signing key; set a secret in shared deployments |
| `LOG_LEVEL` | `INFO` | Python log level |
| `PORT` | `8080` in Docker | Gunicorn listening port |

There is intentionally no authentication requirement by default, making a
loopback installation convenient. Set `SHORTLINK_API_TOKEN` before exposing
the service to a network. The browser management UI remains available; the
token protects API write operations and imports.

## Managing shortlinks

### Web UI &mdash; `http://go/manage`

The manage page gives you:

- An **Add** form with client- and server-side validation
- An **Edit** row for every entry &mdash; change the URL, rename the shortcut, or both
- A **Delete** button with a confirmation prompt
- Flash messages for success/error after every action

Short names must match `[A-Za-z0-9_.-]{1,64}` and may not be one of the
reserved names (`api`, `home`, `healthz`, `listdb`, `manage`, `static`,
`favicon.ico`).

Each link may also have a description and comma-separated tags. Creation and
update timestamps are retained and are included in detailed API responses.

### JSON API

The legacy `GET /api/urls` response is a name-to-URL object. Add
`?details=1` for metadata, filtering, and pagination:

```sh
curl http://go/api/urls?details=1\&q=git\&page=1\&per_page=25
curl http://go/api/urls/github
curl -X PUT http://go/api/urls/github \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://github.com/","description":"Source hosting","tags":["code","git"]}'
curl -X DELETE http://go/api/urls/github
```

When `SHORTLINK_API_TOKEN` is set, write examples need
`-H "Authorization: Bearer $SHORTLINK_API_TOKEN"`. Invalid or malformed JSON,
validation failures, missing links, and server failures return JSON with
`error` and `status` fields.

Export/import use a versioned JSON document:

```sh
curl http://go/api/urls/export > links.json
curl -X POST http://go/api/urls/import -H 'Content-Type: application/json' \
  --data-binary @links.json
```

Import upserts by name and is atomic: an invalid record leaves the database
unchanged.

## Backups

The database is stored in the Docker/Podman `go-shortlink-data` volume.
Back up a live database with SQLite's online backup command (or stop the
container first for a simple file copy):

```sh
docker run --rm -v go-shortlink-data:/data -v "$PWD":/backup \
  python:3.11-slim python -c \
  'import sqlite3; sqlite3.connect("/data/shortlinks.db").backup(sqlite3.connect("/backup/shortlinks.db"))'
```

For Podman, replace `docker` with `podman` and use a volume that is accessible
to the invoking user. Restore only while the service is stopped.

## Docker and Podman

`build.sh`, `run.sh`, and `stop.sh` use Docker CLI syntax. Podman supports the
same commands in most installations; invoke the scripts with
`CONTAINER_ENGINE=podman` (or run the equivalent `podman build/run/stop`
commands). Rootless Podman cannot bind privileged host port 80, so choose
`HOST_PORT=8080` (and use `http://go:8080`) or enable rootful networking.
Docker Desktop manages volumes inside its VM, while rootless Podman stores
them in the user's local container storage. In both cases, keep the named
volume when rebuilding so links survive.


## Development

```sh
make dev      # install runtime + test deps
make test     # run the pytest suite
make run      # start the Flask dev server (auto-reload enabled)
```

The Flask app is constructed by `create_app()` in `app.py`, so tests can
inject an isolated SQLite path:

```python
from app import create_app

app = create_app(db_path="test.db")
```
