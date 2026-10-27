"""gunicorn settings for production: ``gunicorn -c deploy/gunicorn.conf.py config.wsgi``.

Defaults can be overridden with GUNICORN_* environment variables. gunicorn doesn't
read .env/production.env, so set them in the systemd unit (Environment=...).
"""

import multiprocessing
import os

# Listen on localhost only; nginx in front terminates HTTPS and forwards here.
bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:8000")

# (2 x CPU) + 1 is gunicorn's recommendation for sync workers; capped so a big
# server doesn't open more database connections than PostgreSQL allows.
workers = int(os.environ.get("GUNICORN_WORKERS", min(multiprocessing.cpu_count() * 2 + 1, 9)))

# Excel imports (up to 20 MB) can take a while to parse; nginx's proxy_read_timeout matches.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 120))
graceful_timeout = 30
keepalive = 5

# Restart workers now and then so a slow memory leak can never take the server down.
max_requests = 1000
max_requests_jitter = 100

# Only nginx on this machine may set X-Forwarded-* headers.
forwarded_allow_ips = "127.0.0.1"

# Logs to stdout/stderr, collected by systemd's journal (journalctl -u finance_saber).
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
