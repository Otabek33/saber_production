#!/usr/bin/env bash
# Release the latest code on the server. Run as the app user:
#   sudo -u finance_saber /srv/finance_saber/deploy/deploy.sh
# Stops at the first failing step, before the running site is touched.
set -euo pipefail

cd "$(dirname "$0")/.."
export DJANGO_SETTINGS_MODULE=config.settings.production
# Python installed by uv lives outside /home (the service runs with ProtectHome=true).
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/opt/finance_saber/python}"
PYTHON=.venv/bin/python

if [ -d .git ]; then
    echo "==> Fetching code"
    git pull --ff-only
else
    echo "==> Not a git checkout: releasing the code as it is in $(pwd)"
fi

echo "==> Installing dependencies (exact versions from uv.lock)"
uv sync --locked --no-default-groups --group prod --group postgres

echo "==> Checking settings"
$PYTHON manage.py check --deploy --fail-level WARNING

echo "==> Migrating the database"
$PYTHON manage.py migrate --noinput

echo "==> Collecting static files"
$PYTHON manage.py collectstatic --noinput --clear --verbosity 0

echo "==> Reloading the app (graceful: running requests finish first)"
sudo systemctl reload finance_saber

echo "==> Health check"
HOST=$($PYTHON manage.py shell -c "from django.conf import settings; print(settings.ALLOWED_HOSTS[0])")
# The address gunicorn listens on, as setup_server.sh set it for this server.
BIND=$(systemctl show -p Environment --value finance_saber | tr ' ' '\n' | sed -n 's/^GUNICORN_BIND=//p')
BIND=${BIND:-127.0.0.1:8000}
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS -H "Host: $HOST" "http://$BIND/health/"; then
        echo
        echo "==> Deployed."
        exit 0
    fi
    sleep 2
done
echo "Health check failed; see: journalctl -u finance_saber -n 100" >&2
exit 1
