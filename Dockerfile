FROM python:3.11.9-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    SHORTLINK_DB_PATH=/data/shortlinks.db

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY templates/ ./templates/
COPY static/ ./static/

RUN mkdir -p /data \
    && useradd -ms /bin/bash flaskapp \
    && chown -R flaskapp:flaskapp /data /app

VOLUME ["/data"]
EXPOSE 8080

USER flaskapp

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 2 --access-logfile - app:app"]
