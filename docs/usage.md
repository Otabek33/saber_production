# Settings, database and deployment

## Settings layout

```
config/settings/
├── base.py         # shared by every environment
├── local.py        # development (default for manage.py)
├── production.py   # servers (default for wsgi.py / asgi.py)
└── test.py         # pytest
```

Choose one with `DJANGO_SETTINGS_MODULE`. Secrets and host-specific values
come from environment variables, or from the env file in the `.env/` folder
that matches the settings module:

```
.env/
├── local.env                 # read by config.settings.local
├── local.env.example         # committed template for local.env
├── production.env            # read by config.settings.production
└── production.env.example    # committed template for production.env
```

Only the `*.example` files are committed. Real environment variables override
the env file. Tests use in-memory SQLite and read no env file.

## PostgreSQL

The database is configured by a single `DATABASE_URL`. With it unset you get
SQLite at `db.sqlite3`. Both env file templates use PostgreSQL:

1. Install the driver: `uv sync --group postgres`
2. Set the URL in `.env/local.env` (or `.env/production.env`, or the server environment):

    ```sh
    DATABASE_URL=postgres://finance_saber:password@localhost:5432/finance_saber
    ```

3. `uv run python manage.py migrate`

To keep existing SQLite data, export it first with
`python manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.permission > data.json`,
then after migrating on PostgreSQL run `python manage.py loaddata data.json`.

## Production

Set at least these variables, in `.env/production.env` (copied from
`.env/production.env.example`) or in the server environment:

| Variable | Example |
| --- | --- |
| `DJANGO_SETTINGS_MODULE` | `config.settings.production` |
| `DJANGO_SECRET_KEY` | a long random string |
| `DJANGO_ALLOWED_HOSTS` | `example.com,www.example.com` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://example.com` |
| `DATABASE_URL` | `postgres://...` |
| `DJANGO_ADMIN_URL` | `some-hard-to-guess-path/` |

Production refuses to start without `DATABASE_URL` (there is no SQLite
fallback on a server). `manage.py` defaults to the *local* settings, so on the
server always export `DJANGO_SETTINGS_MODULE=config.settings.production` before
running management commands.

### Server setup (Ubuntu/Debian, nginx + gunicorn + systemd)

The `deploy/` folder has everything the server needs:

| File | What it is |
| --- | --- |
| `deploy/gunicorn.conf.py` | gunicorn: workers, timeouts, listens on `127.0.0.1:8000` |
| `deploy/systemd/finance_saber.service` | runs the app, restarts it on failure |
| `deploy/systemd/finance_saber-daily-reports.{service,timer}` | `send_daily_reports` every 5 minutes |
| `deploy/nginx/finance_saber.conf` | HTTPS, HTTP→HTTPS redirect, 25 MB uploads |
| `deploy/deploy.sh` | one command for every release |
| `deploy/setup_server.sh` | first install on a fresh server, all of the steps below in one command |

**First install in one command.** Point the domain's DNS A record at the server, put
the code in `/srv/finance_saber` (or let the script clone it), then as root:

```sh
sudo DOMAIN=work.fintechdev.uz CERTBOT_EMAIL=you@example.com bash /srv/finance_saber/deploy/setup_server.sh
```

It installs nginx, PostgreSQL, certbot and uv. It creates the `finance_saber` user,
and a PostgreSQL user and database with a random password. It writes
`.env/production.env` with a new secret key and admin address, gets the HTTPS
certificate, and installs the services and the nginx site. Then it does the first
release with `deploy.sh`. Running it again is safe: it keeps the existing
password, keys and data. To move to the real domain later, run it again with
`DOMAIN=new.domain.uz`: the hosts, nginx and certificate follow. When the repository is
private and the code is not on the server yet, the script prints an SSH key: add it on
GitHub as a read-only deploy key, then run it again.

To use a PostgreSQL database you created yourself, name it and its user. The script
asks for the password (hidden), checks that it connects, and writes `DATABASE_URL`
with the password percent-encoded:

```sh
sudo DB_NAME=saber_finance DB_USER=saber DOMAIN=work.fintechdev.uz bash /srv/finance_saber/deploy/setup_server.sh
```

On a server that runs other sites, nothing of theirs is changed: the default nginx
site, their certificates and certbot stay as they are. The app listens on
`127.0.0.1:8000`; if another program uses that port the script stops and shows it. Pick a
free one (`ss -ltnp` lists the ports in use) and pass `PORT=8010`, and optionally
`WORKERS=3` to keep gunicorn small next to the other apps. nginx 1.18/1.24 (Ubuntu
22.04/24.04) get `http2` on the `listen` line automatically.

Django 6.1 needs PostgreSQL 15 or later. Ubuntu 22.04 ships 14: there the script adds
the official PostgreSQL repository and installs PostgreSQL 17 next to 14, on its own
port (usually 5433). The database is made there, and the other sites' databases on 14
are not touched. `DB_PORT=` picks a cluster yourself; `pg_lsclusters` lists them.

The same steps by hand, as root (replace `finance.example.uz` with your domain in the nginx file):

```sh
apt install -y git nginx postgresql certbot python3-certbot-nginx
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# Database
sudo -u postgres createuser finance_saber --pwprompt
sudo -u postgres createdb finance_saber --owner finance_saber

# App user and code
useradd --system --create-home --shell /bin/bash finance_saber
install -d -o finance_saber -g finance_saber /srv/finance_saber
sudo -u finance_saber git clone git@github.com:Otabek33/saber_production.git /srv/finance_saber
cd /srv/finance_saber
sudo -u finance_saber cp .env/production.env.example .env/production.env
sudo -u finance_saber nano .env/production.env      # fill in every value
chmod 600 .env/production.env

# Let the app user reload the app during deploys
echo 'finance_saber ALL=(root) NOPASSWD: /usr/bin/systemctl reload finance_saber' > /etc/sudoers.d/finance_saber

# Services
cp deploy/systemd/finance_saber*.service deploy/systemd/finance_saber*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable finance_saber finance_saber-daily-reports.timer

# HTTPS and nginx
certbot certonly --nginx -d finance.example.uz
cp deploy/nginx/finance_saber.conf /etc/nginx/sites-available/finance_saber
ln -s /etc/nginx/sites-available/finance_saber /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# First release, then the first administrator
sudo -u finance_saber uv sync --locked --no-default-groups --group prod --group postgres
systemctl start finance_saber finance_saber-daily-reports.timer
sudo -u finance_saber /srv/finance_saber/deploy/deploy.sh
sudo -u finance_saber env DJANGO_SETTINGS_MODULE=config.settings.production .venv/bin/python manage.py createsuperuser
```

### Handbooks on the new server

The new database has only the handbook the migrations create (the base articles,
categories and regions) and no counterparties. Bring over the full handbooks
before the first «Загрузить Excel» of the finance workbook, so bank lines are
classified by their counterparty from the first day:

The handbooks travel with the code: `finance_saber/cashbook/seed/handbook.xlsx`
(articles and categories) and `seed/counterparties.xlsx` (counterparties and
regions) are committed and deployed like any other file.

1. Where the handbooks are complete (your computer), refresh the project's files
   and commit them: `uv run python manage.py export_handbooks --seed`.
   **They hold real INNs and bank account numbers: keep the repository private.**
   They are never served as public static files, only to signed-in users.
2. On the server, sign in as the administrator and open
   **Касса › Справочник › Загрузить из Excel**. Under «Файлы проекта», click
   «Загрузить файлы проекта» with «Только проверить» ticked, read the result, then
   again unticked. The handbook is loaded first, then the counterparties.
   Or download them with «Файл из проекта» (on Справочник and Контрагенты) and
   upload them one by one, handbook first. From a shell:
   `sudo -u finance_saber env DJANGO_SETTINGS_MODULE=config.settings.production .venv/bin/python manage.py load_handbooks finance_saber/cashbook/seed/handbook.xlsx`
   and then `.../seed/counterparties.xlsx` (add `--dry-run` to check first).

Records are matched by name (counterparties by their statement reference), so
loading the file again changes nothing; if any row has an error, nothing is saved.

### Recipes on the new server

Products, raw materials and packaging, recipes and the products' other names
(SmartUp) travel with the code too: `finance_saber/production/seed/recipes.xlsx`.

1. Where the recipes are complete, refresh the file and commit it:
   `uv run python manage.py export_recipes --seed`.
2. On the server open **Производство › Рецептуры и нормы › Загрузить из Excel**.
   Under «Файл проекта», click «Загрузить файл проекта» with «Только проверить»
   ticked, read the result, then again unticked. From a shell:
   `sudo -u finance_saber env DJANGO_SETTINGS_MODULE=config.settings.production .venv/bin/python manage.py load_recipes`
   (the project's file; add `--dry-run` to check first).

The same page loads any file from «Скачать Excel», e.g. recipes corrected in
Excel. Records are matched by name, so loading again changes nothing. A product
listed on «Рецептуры» gets exactly the file's lines (lines not in the file are
removed); products not listed keep their recipe, and production already entered
keeps the norms it was made with. «Норма на 1 шт» is what is loaded; «Норма на
1000 л» is used only where the norm per unit is empty. If any row has an error,
nothing is saved.

### Raw materials and their stock on the new server

`finance_saber/production/seed/materials.xlsx` holds the materials and packaging
with their stock («Остаток»). Refresh it with
`uv run python manage.py export_materials --seed` and commit it.

On the server, load it alongside the recipes file (both carry the materials list;
only this one carries stock): **Производство › Сырьё на складе › Загрузить из Excel**, then
«Загрузить файл проекта» with «Только проверить» ticked, then unticked. From a
shell: `... manage.py load_materials` (add `--dry-run` to check first).

Stock is a ledger of movements, so «Остаток» is loaded only when you tick
«Загрузить столбец «Остаток» как начальные остатки» (or pass
`--opening-date YYYY-MM-DD`): each material gets an opening balance on that date.
Do this only when the file's figures are a real count on that date — ideally
count the warehouse on go-live day, enter the figures in the file, and load it.
Materials that already have stock movements are never touched (the page lists
them); on a server already in use, correct stock with «Инвентаризация» instead.
Loading the file again with corrected figures corrects the opening balances, it
never adds to them.

If Telegram is configured, point the bot at the site once:
`sudo -u finance_saber env DJANGO_SETTINGS_MODULE=config.settings.production .venv/bin/python manage.py set_telegram_webhook https://finance.example.uz`
(from `/srv/finance_saber`).

### Every release

```sh
sudo -u finance_saber /srv/finance_saber/deploy/deploy.sh
```

It pulls the code, installs the exact versions in `uv.lock`, runs
`check --deploy`, migrates, collects static files, reloads gunicorn gracefully
and checks `/health/`. It stops at the first failure.

Logs: `journalctl -u finance_saber -f` (app) and `/var/log/nginx/finance_saber.*.log`.

### Notes

- Static files are served by WhiteNoise from `staticfiles/`; nginx only proxies.
- `DJANGO_SECURE_HSTS_SECONDS` defaults to 60 — raise it to `31536000` once
  HTTPS is confirmed working.
- Back up the database daily, e.g. a cron job running
  `pg_dump -Fc finance_saber > /var/backups/finance_saber-$(date +%F).dump`,
  and copy the dumps off the server.
- After changing dependencies in `pyproject.toml`, run `uv lock` and commit
  `uv.lock`; the server only installs what the lockfile pins.

### Excel uploads on the server

On a server, nobody's Excel files are on disk, so workbooks are uploaded through
the app instead of read from a path:

- **Production › Import from Excel**: the report workbook (sheet «Сбор») and the
  finance workbook (sheet «Warehouse»). "Only check" shows what would be imported
  without saving; importing again only adds what is missing.
- **Cash desk › Upload Excel**: the finance workbook (Situation.xlsx), once for
  every desk. Sheets «Касса Бон», «Инкасса & Розница» and «Банк» go to their
  desks (names are matched ignoring case and spaces). "Check" shows each desk's
  new rows, rows changed in Excel since they were imported, balance differences
  and bank counterparties not in the app yet; "Import" then adds only what is
  missing, skipping any desk with errors. For new counterparties you choose: add
  them automatically, or skip their lines until you have created them yourself.
- **Cash desk › Bank › Upload statement**: the bank's own daily export.

Files are read in memory and not kept, with one exception: between "Check" and
"Import" the finance workbook waits in the system temp folder
(`/tmp/finance_saber_uploads/`, private to the service under systemd) and is
deleted after the import, or after two hours. No media volume is needed.
What the server needs:

- The reverse proxy must accept uploads of the size you use; nginx's default is
  1 MB. Set `client_max_body_size 25m;` (the app itself rejects workbooks over 20 MB).
- Save the workbooks in Excel before uploading. Formula cells are read as the values
  Excel saved with them; a recipe norm missing its value is recalculated from the
  dose per 1000 l, but other formulas are not.
- The `import_production_xlsx` and `import_*_xlsx` management commands do the same
  from files on disk; use them when you have shell access and the files are copied
  to the server, e.g. for a first load.

### Users and roles

Roles are defined in `finance_saber/users/roles.py` and written to the database
by every `migrate`, so every server has exactly the same ones:

| Role | Works in | Views |
| --- | --- | --- |
| **Finance** (Финансист) | cash desks and bank, handbook and counterparties, exchange rates, cash plan and collections, sales and clients | production and stock |
| **Planner** (Планировщик) | sales and production plan, cash plan, collections | cash desks, sales, production and stock |
| **Technologist** (Технолог) | recipes and norms, daily production and calculation, raw materials and stock, finished goods, production import, production plan | sales |
| **Operator** (Оператор) | the day's sales (enter, correct, cancel) and the sales import | clients, debts, sales channels |
| **Manager** (Руководитель) | every business section | — |
| **Superuser** | everything, including Administration (users, roles and permissions) | — |

The superuser manages people under **Administration**: *Users* (create a user with
a role and a first password, edit details and role, block sign-in, set a new
password) and *Roles and permissions* (what each role may do, section by section).

The table above is each role's *standard* access. On *Roles and permissions*,
"Edit access" changes a role section by section — No access, Views or Works —
and the change applies to everyone with that role at once. Changes are stored in
the database (not in the code), so they stay after updates and `migrate`; each one
is logged with who made it and when, and "Reset to standard" removes them. Users,
roles and permissions are never given to a role: that stays with the superuser.
People who sign up themselves get no role and see no data until the superuser gives
them one. Users are never deleted: untick "Can sign in", so their records keep their
author. To change a role's *standard* access for every server, edit `STANDARD_ACCESS`
in `roles.py` and run `migrate`; a new section goes into `AREAS` with its permissions.

On a new server, after `migrate`, create the first administrator:

```sh
uv run python manage.py createsuperuser
# or give an existing account a role: superuser, finance, planner, technologist, operator, director or none
uv run python manage.py set_role <username> superuser
```
