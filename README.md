# go-shortlink

A tiny, self-hosted URL shortener for personal use. Point your browser at
`http://go/<name>` and get redirected to whatever URL `<name>` resolves to.
Inspired by the `go/` short-links many companies run internally.

```
http://go/github   ->  https://github.com/
http://go/mail     ->  https://mail.google.com/
http://go/manage   ->  Web UI to add / edit / delete shortlinks
http://go/listdb   ->  Read-only HTML table of every registered shortlink
```

## Features

- **SQLite storage** &mdash; durable, transactional, single-file database
- **Web UI at `/manage`** &mdash; add, rename, edit, and delete shortlinks
- **REST API** for scripting and integration
- `/healthz` endpoint and Docker `HEALTHCHECK`
- Production container runs `gunicorn` as a non-root user
- Test suite (`pytest`)

## Quick start

### Option A &mdash; Docker (recommended)

```sh
./build.sh          # or: make docker-build
./run.sh            # or: make docker-run
./stop.sh           # or: make docker-stop
```

`run.sh` creates a Docker volume (`go-shortlink-data`) for the SQLite database
so entries survive container restarts and rebuilds.

### Option B &mdash; Run locally without Docker

```sh
pip install -r requirements.txt
./shortlink-start.sh        # listens on http://127.0.0.1:8080
```

A `shortlinks.db` file will be created in the project directory on first run.

### Make `http://go/...` resolve

Add an alias for `go` to your hosts file:

```sh
# /etc/hosts
127.0.0.1   localhost   go
```

Once the container is bound to host port 80, `http://go/<name>` will hit the
service. On macOS / Linux you'll need to run `./run.sh` with `sudo` (or change
`HOST_PORT` to something `>1024` &mdash; see [Configuration](#configuration)).

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

### REST API

| Method | Path                  | Description                                  |
| ------ | --------------------- | -------------------------------------------- |
| GET    | `/api/urls`           | JSON dump of every shortlink                 |
| GET    | `/api/urls/<name>`    | JSON entry for `<name>` (404 if missing)     |
| PUT    | `/api/urls/<name>`    | Create or update (body: `{"url":"..."}`)     |
| DELETE | `/api/urls/<name>`    | Remove `<name>`                              |

```sh
curl http://go/api/urls

curl -X PUT http://go/api/urls/anthropic \
     -H 'Content-Type: application/json' \
     -d '{"url": "https://www.anthropic.com/"}'

curl -X DELETE http://go/api/urls/anthropic
```

Only `http://` and `https://` URLs are accepted. PUT returns `201` on create
and `200` on update.

### Full endpoint table

| Method | Path                       | Description                              |
| ------ | -------------------------- | ---------------------------------------- |
| GET    | `/`                        | Redirects to `/home`                     |
| GET    | `/home`                    | Landing page                             |
| GET    | `/manage`                  | Management UI                            |
| POST   | `/manage/add`              | Form: create shortlink                   |
| POST   | `/manage/edit/<name>`      | Form: update / rename                    |
| POST   | `/manage/delete/<name>`    | Form: delete                             |
| GET    | `/listdb`                  | Read-only HTML table                     |
| GET    | `/healthz`                 | JSON liveness probe                      |
| GET    | `/<name>`                  | 302 redirect for `<name>`                |
| ...    | `/api/urls[/<name>]`       | REST API (see above)                     |

## Storage

- The active store is a SQLite database (default: `./shortlinks.db` locally,
  `/data/shortlinks.db` in Docker).
- Schema:

  ```sql
  CREATE TABLE shortlinks (
      name       TEXT PRIMARY KEY,
      url        TEXT NOT NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
  ```

- Back up by copying the file (`cp shortlinks.db backup.db`) or by exporting
  JSON via `curl http://go/api/urls > backup.json`.
- To restore from a JSON backup, replay it through the API:
  ```sh
  jq -r 'to_entries[] | "\(.key) \(.value)"' backup.json | \
    while read n u; do
      curl -X PUT "http://go/api/urls/$n" -H 'Content-Type: application/json' \
           -d "{\"url\":\"$u\"}"
    done
  ```

## Configuration

Set via environment variables.

| Variable               | Default                              | Description                                  |
| ---------------------- | ------------------------------------ | -------------------------------------------- |
| `SHORTLINK_DB_PATH`    | `./shortlinks.db` (local) / `/data/shortlinks.db` (Docker) | Path to the SQLite file |
| `FLASK_SECRET_KEY`     | dev placeholder                      | Secret key for flash-message signing         |
| `FLASK_RUN_HOST`       | `127.0.0.1`                          | Bind host (local dev server)                 |
| `FLASK_RUN_PORT`       | `8080`                               | Bind port (local dev server)                 |
| `FLASK_DEBUG`          | `0`                                  | Enable Flask debug mode                      |
| `LOG_LEVEL`            | `INFO`                               | Python logging level                         |
| `PORT`                 | `8080`                               | gunicorn bind port inside the container      |

`run.sh` recognises a few more for the host side:

| Variable     | Default              | Description                          |
| ------------ | -------------------- | ------------------------------------ |
| `IMAGE`      | `go-shortlink-img`   | Docker image name                    |
| `TAG`        | `latest`             | Docker image tag                     |
| `CONTAINER`  | `go-shortlink-svc`   | Container name                       |
| `HOST_BIND`  | `127.0.0.1`          | Host interface to bind to            |
| `HOST_PORT`  | `80`                 | Host port mapped to container 8080   |
| `DATA_VOLUME`| `go-shortlink-data`  | Docker volume name backing `/data`   |

Example: run on `localhost:8000` instead of port 80:

```sh
HOST_PORT=8000 ./run.sh
# now: http://localhost:8000/manage
```

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

app = create_app(db_path="/tmp/test.db")
```

## Repository layout

```
app.py                  # Flask application + ShortlinkDB (SQLite)
templates/              # Jinja templates (home / list / manage / 404)
static/css/             # Stylesheets
tests/                  # pytest suite
Dockerfile              # Production image (gunicorn, non-root user)
build.sh, run.sh, stop.sh, shortlink-start.sh   # Convenience wrappers
Makefile                # `make help` for the full command list
```

## Troubleshooting

- **`http://go/...` doesn't resolve** &mdash; check `/etc/hosts` contains the `go`
  alias, then `ping go` to verify.
- **Permission denied binding to port 80** &mdash; either run with `sudo` or set
  `HOST_PORT` to a port above 1024.
- **Lost data after `docker rm`** &mdash; the SQLite file lives in the
  `go-shortlink-data` volume; don't `docker volume rm` it unless you mean to
  wipe the database.
- **Container won't start** &mdash; `make docker-logs` (or `docker logs go-shortlink-svc`).

## License

See [LICENSE](./LICENSE).
