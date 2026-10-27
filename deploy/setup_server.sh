#!/usr/bin/env bash
# First install of finance_saber on a fresh Ubuntu/Debian server, as root:
#   sudo DOMAIN=work.fintechdev.uz CERTBOT_EMAIL=you@example.com bash deploy/setup_server.sh
#
# Safe to run again: every step checks what is already there. Run it again with a
# new DOMAIN to move the site to a new address (env file, nginx and certificate follow).
# For later releases use deploy/deploy.sh, not this script.
#
# Before running:
#   - the domain's DNS A record points at this server (Let's Encrypt checks it);
#   - the code is in /srv/finance_saber (copied there), or REPO_URL can be cloned. For a
#     private repository the script makes an SSH key for the app user and prints it: add it
#     on GitHub › Settings › Deploy keys (read-only), then run the script again.
#
# Database: by default the script creates the PostgreSQL user and database finance_saber
# with a random password. To use ones you already created, name them; the script asks
# for the password (hidden, kept out of the shell history) and checks that it connects:
#   sudo DB_NAME=saber_finance DB_USER=saber bash deploy/setup_server.sh
#
# Shared server: other sites keep running. The app listens on 127.0.0.1:PORT (8000 by
# default); if another program already uses that port the script stops, so pick a free
# one (see: ss -ltnp) with PORT=8010. WORKERS sets gunicorn's processes (default 2 x CPU + 1).
set -euo pipefail

DOMAIN="${DOMAIN:-work.fintechdev.uz}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
REPO_URL="${REPO_URL:-git@github.com:Otabek33/finance_saber.git}"
BRANCH="${BRANCH:-master}"
DB_NAME="${DB_NAME:-}"
DB_USER="${DB_USER:-}"
DB_PASSWORD="${DB_PASSWORD:-}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-}"

# Fixed paths: the systemd units in deploy/systemd/ use them.
APP=finance_saber
APP_DIR=/srv/finance_saber
ENV_FILE="$APP_DIR/.env/production.env"
# Python installed by uv lives outside /home: the service runs with ProtectHome=true.
export UV_PYTHON_INSTALL_DIR=/opt/finance_saber/python

step() { echo; echo "==> $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }
as_app() { sudo -u "$APP" -H env UV_PYTHON_INSTALL_DIR="$UV_PYTHON_INSTALL_DIR" "$@"; }
manage() { as_app env DJANGO_SETTINGS_MODULE=config.settings.production "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" "$@"; }

# Sets KEY=VALUE in the env file: replaces the line (commented out or not) or appends it.
set_env() {
    local key="$1" value="$2" tmp
    tmp=$(mktemp)
    awk -v k="$key" -v v="$value" '
        $0 ~ "^#? *" k "=" { if (!done) print k "=" v; done = 1; next }
        { print }
        END { if (!done) print k "=" v }
    ' "$ENV_FILE" > "$tmp"
    cat "$tmp" > "$ENV_FILE"
    rm -f "$tmp"
}
get_env() { grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- || true; }

[ "$(id -u)" -eq 0 ] || fail "run as root: sudo bash $0"
cd /tmp  # sudo -u postgres can't read root's home folder
[[ "$DOMAIN" =~ ^[a-z0-9.-]+$ ]] || fail "DOMAIN looks wrong: $DOMAIN"
[[ "$PORT" =~ ^[0-9]+$ ]] || fail "PORT must be a number: $PORT"
[[ "$WORKERS" =~ ^[0-9]*$ ]] || fail "WORKERS must be a number: $WORKERS"
# The port must be free, or already ours (running the script again).
listener=$(ss -ltnpH "sport = :$PORT" 2>/dev/null || true)
if [ -n "$listener" ]; then
    our_pid=$(systemctl show -p MainPID --value finance_saber 2>/dev/null || echo 0)
    if [ "${our_pid:-0}" = "0" ] || ! grep -q "pid=$our_pid," <<< "$listener"; then
        echo "$listener" >&2
        fail "port $PORT is used by another program (above); choose a free one, e.g. PORT=8010"
    fi
fi

step "1. System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
packages=(git curl ca-certificates openssl python3 nginx postgresql)
# Keep a certbot already installed (e.g. from snap) for the other sites; add apt's only if none.
command -v certbot >/dev/null 2>&1 || packages+=(certbot)
apt-get install -y -qq "${packages[@]}" >/dev/null
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi
uv --version

step "2. App user and code in $APP_DIR"
id "$APP" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "$APP"
install -d -o "$APP" -g "$APP" "$UV_PYTHON_INSTALL_DIR"
install -d -o "$APP" -g "$APP" "$APP_DIR"
if [ ! -f "$APP_DIR/manage.py" ]; then
    # The app user clones, so deploy.sh (run as that user) can pull later with the same key.
    if [[ "$REPO_URL" == git@* ]]; then
        home=$(getent passwd "$APP" | cut -d: -f6)
        if [ ! -f "$home/.ssh/id_ed25519" ]; then
            as_app install -d -m 700 "$home/.ssh"
            as_app ssh-keygen -q -t ed25519 -N "" -C "$APP@$(hostname)" -f "$home/.ssh/id_ed25519"
        fi
        ssh-keyscan -t ed25519 github.com 2>/dev/null | as_app tee -a "$home/.ssh/known_hosts" >/dev/null
    fi
    if ! as_app git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"; then
        [[ "$REPO_URL" == git@* ]] && echo "Add this key on GitHub › the repository › Settings › Deploy keys, then run again:"             && cat "$home/.ssh/id_ed25519.pub"
        fail "could not clone $REPO_URL"
    fi
fi
chown -R "$APP:$APP" "$APP_DIR"

step "3. Settings: $ENV_FILE"
install -d -o "$APP" -g "$APP" -m 700 "$APP_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    install -o "$APP" -g "$APP" -m 600 "$APP_DIR/.env/production.env.example" "$ENV_FILE"
    # Letters and digits only: nothing to escape in the env file.
    set_env DJANGO_SECRET_KEY "$(openssl rand -base64 64 | tr -dc 'A-Za-z0-9' | head -c 60)"
    set_env DJANGO_ADMIN_URL "admin-$(openssl rand -hex 8)/"
    echo "Created with a new secret key and admin address."
fi
# The address may change (the domain is temporary): these follow DOMAIN on every run.
set_env DJANGO_ALLOWED_HOSTS "$DOMAIN"
set_env DJANGO_CSRF_TRUSTED_ORIGINS "https://$DOMAIN"
set_env DJANGO_SITE_URL "https://$DOMAIN"
chown "$APP:$APP" "$ENV_FILE"
chmod 600 "$ENV_FILE"

step "4. PostgreSQL database"
systemctl enable --now postgresql
psql_root() { sudo -u postgres psql -v ON_ERROR_STOP=1 -qtAX "$@"; }
database_url=$(get_env DATABASE_URL)
if [ -n "$DB_NAME$DB_USER" ]; then
    # A database created by hand: use it as it is, never change its user or password.
    [ -n "$DB_NAME" ] && [ -n "$DB_USER" ] || fail "give both DB_NAME and DB_USER"
    if [[ "$database_url" != "postgres://$DB_USER:"*"@localhost:5432/$DB_NAME" ]] || [ -n "$DB_PASSWORD" ]; then
        if [ -z "$DB_PASSWORD" ]; then
            [ -t 0 ] || fail "no terminal to ask for the password: set DB_PASSWORD"
            read -rsp "Password of PostgreSQL user $DB_USER: " DB_PASSWORD
            echo
        fi
        PGPASSWORD="$DB_PASSWORD" psql -h localhost -U "$DB_USER" -d "$DB_NAME" -qtAX -c "SELECT 1" >/dev/null             || fail "cannot connect to database $DB_NAME as $DB_USER with that password"
        # Percent-encode the password: characters like ! @ : / # would break the URL.
        encoded=$(DB_PASSWORD="$DB_PASSWORD" python3 -c 'import os, urllib.parse; print(urllib.parse.quote(os.environ["DB_PASSWORD"], safe=""))')
        set_env DATABASE_URL "postgres://$DB_USER:$encoded@localhost:5432/$DB_NAME"
        unset DB_PASSWORD encoded
        echo "Using database $DB_NAME as $DB_USER (connection checked)."
    fi
    # PostgreSQL 15+ no longer lets every user create tables in "public"; the owner can.
    [ "$(psql_root -d "$DB_NAME" -c "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '$DB_NAME'")" = "$DB_USER" ]         || psql_root -d "$DB_NAME" -c "GRANT USAGE, CREATE ON SCHEMA public TO \"$DB_USER\""
else
    if [ -z "$database_url" ] || [[ "$database_url" == *":password@"* ]]; then
        # New password, written only to the env file (hex: safe inside a URL).
        db_password=$(openssl rand -hex 24)
        if [ "$(psql_root -c "SELECT 1 FROM pg_roles WHERE rolname = '$APP'")" = "1" ]; then action=ALTER; else action=CREATE; fi
        # Through stdin, so the password never shows in the process list.
        psql_root <<< "$action ROLE $APP WITH LOGIN PASSWORD '$db_password'"
        set_env DATABASE_URL "postgres://$APP:$db_password@localhost:5432/$APP"
        unset db_password
        echo "Database user $APP has a new password (in the env file)."
    fi
    if [ "$(psql_root -c "SELECT 1 FROM pg_database WHERE datname = '$APP'")" != "1" ]; then
        sudo -u postgres createdb --owner "$APP" --encoding UTF8 --template template0 "$APP"
        echo "Database $APP created."
    fi
fi

step "5. Python and dependencies (exact versions from uv.lock)"
(cd "$APP_DIR" && as_app uv sync --locked --no-default-groups --group prod --group postgres)

step "6. systemd services"
cp "$APP_DIR"/deploy/systemd/finance_saber*.service "$APP_DIR"/deploy/systemd/finance_saber*.timer /etc/systemd/system/
# The app user may only reload the app (deploy.sh does it), nothing else.
sudoers=$(mktemp)
echo "$APP ALL=(root) NOPASSWD: /usr/bin/systemctl reload finance_saber" > "$sudoers"
visudo -cqf "$sudoers" || fail "sudoers rule is invalid"
install -m 440 "$sudoers" /etc/sudoers.d/finance_saber
rm -f "$sudoers"
# Port and workers for this server, kept outside the unit file so updates of it keep them.
install -d /etc/systemd/system/finance_saber.service.d
{
    echo "[Service]"
    echo "Environment=GUNICORN_BIND=127.0.0.1:$PORT"
    [ -z "$WORKERS" ] || echo "Environment=GUNICORN_WORKERS=$WORKERS"
} > /etc/systemd/system/finance_saber.service.d/server.conf
systemctl daemon-reload
systemctl enable finance_saber finance_saber-daily-reports.timer

step "7. HTTPS certificate for $DOMAIN"
NGINX_SITE=/etc/nginx/sites-available/finance_saber
# Other sites on this server are left alone: only the finance_saber site is written.
install -d /var/www/html
if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    # nginx can't load the HTTPS site before the certificate exists: serve only the challenge first.
    cat > "$NGINX_SITE" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 503; }
}
EOF
    ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/finance_saber
    nginx -t && systemctl reload nginx
    if [ -n "$CERTBOT_EMAIL" ]; then email_args=(--email "$CERTBOT_EMAIL"); else email_args=(--register-unsafely-without-email); fi
    certbot certonly --webroot -w /var/www/html -d "$DOMAIN" --agree-tos --non-interactive "${email_args[@]}" \
        || fail "no certificate: check that $DOMAIN points at this server and ports 80/443 are open"
fi
# Renewals (certbot's timer) reload nginx so it picks up the new certificate.
install -d /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nsystemctl reload nginx\n' > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx

step "8. nginx site"
sed -e "s/finance\.example\.uz/$DOMAIN/g" -e "s/127\.0\.0\.1:8000/127.0.0.1:$PORT/" \
    "$APP_DIR/deploy/nginx/finance_saber.conf" > "$NGINX_SITE"
# "http2 on;" needs nginx 1.25.1+; Ubuntu 22.04/24.04 ship 1.18/1.24, which take it on "listen".
nginx_version=$(nginx -v 2>&1 | sed -n 's|.*nginx/\([0-9.]*\).*|\1|p')
if dpkg --compare-versions "$nginx_version" lt 1.25.1; then
    sed -i -e '/^[[:space:]]*http2 on;/d' -e 's/listen \(.*\)443 ssl;/listen \1443 ssl http2;/' "$NGINX_SITE"
fi
ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/finance_saber
nginx -t && systemctl reload nginx
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    ufw allow OpenSSH >/dev/null
    ufw allow "Nginx Full" >/dev/null
fi

step "9. First release: check, migrate, static files, start"
# restart, not start: a run with a new PORT or WORKERS must take effect.
systemctl restart finance_saber
as_app "$APP_DIR/deploy/deploy.sh"
systemctl start finance_saber-daily-reports.timer

step "10. From the outside"
# Some hosts can't reach their own public address: then check it from a browser instead.
curl -fsS --max-time 15 "https://$DOMAIN/health/" && echo     || echo "WARNING: https://$DOMAIN/health/ did not answer from here; open it in a browser."
if [ -n "$(get_env TELEGRAM_BOT_TOKEN)" ] && [ -n "$(get_env TELEGRAM_WEBHOOK_SECRET)" ]; then
    manage set_telegram_webhook "https://$DOMAIN" || echo "Telegram webhook not set; run it later."
fi

if ! manage shell -c "from django.contrib.auth import get_user_model; import sys; sys.exit(0 if get_user_model().objects.filter(is_superuser=True).exists() else 1)"; then
    admin_hint="Create the first administrator:
    cd $APP_DIR && sudo -u $APP env DJANGO_SETTINGS_MODULE=config.settings.production .venv/bin/python manage.py createsuperuser"
else
    admin_hint="An administrator already exists."
fi

cat <<EOF

==> Done: https://$DOMAIN
    Admin:     https://$DOMAIN/$(get_env DJANGO_ADMIN_URL)
    Settings:  $ENV_FILE (Telegram, email: fill in, then: systemctl restart finance_saber)
    Logs:      journalctl -u finance_saber -f
    Releases:  sudo -u $APP $APP_DIR/deploy/deploy.sh

$admin_hint

Then load the project's files (docs/usage.md › "on the new server"), in this order:
    Касса › Справочник › Загрузить из Excel › Загрузить файлы проекта
    Производство › Сырьё на складе › Загрузить из Excel › Загрузить файл проекта
    Производство › Рецептуры и нормы › Загрузить из Excel › Загрузить файл проекта
EOF
