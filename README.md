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
service. On macOS / Linux you'll need to enable --rootful is using podman or sudo ./run.sh for docker.

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

