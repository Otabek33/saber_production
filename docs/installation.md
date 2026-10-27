# Installation

## Local development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/Otabek33/finance_saber
cd finance_saber
uv sync --group postgres                    # installs Django + dev tools into .venv
cp .env/local.env.example .env/local.env    # then edit values as needed
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Open <http://127.0.0.1:8000/> (admin at `/admin/`, health check at `/health/`).

Using plain pip instead of uv:

```sh
python -m venv .venv
.venv\Scripts\activate       # Windows; use `source .venv/bin/activate` elsewhere
pip install -e . --group dev
```
