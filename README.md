# finance saber

finance saber automation, built with Django.

* [GitHub](https://github.com/Otabek33/finance_saber/) | [Documentation](https://Otabek33.github.io/finance_saber/)
* Created by [Otabek Davronov](https://audrey.feldroy.com/) | GitHub [@Otabek33](https://github.com/Otabek33)
* MIT License

## Quickstart

```bash
uv sync --group postgres
cp .env/local.env.example .env/local.env
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

## Project layout

```
config/                 # project configuration
  settings/
    base.py             # shared settings
    local.py            # development (manage.py default)
    production.py       # production (wsgi/asgi default)
    test.py             # pytest
  urls.py  wsgi.py  asgi.py
finance_saber/          # Django apps
  core/                 # home page + /health/
  users/                # custom User model
  templates/  static/
deploy/                 # gunicorn, systemd, nginx and deploy.sh for the server
manage.py
uv.lock                 # exact dependency versions; the server installs these
.env/
  local.env.example       # copy to local.env (git-ignored)
  production.env.example  # copy to production.env on the server (git-ignored)
```

PostgreSQL is used locally and in production, via `DATABASE_URL`; see
[docs/usage.md](docs/usage.md) for database setup and production deployment.

## Documentation

Full documentation is available on
[GitHub Pages](https://Otabek33.github.io/finance_saber/).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and
documentation instructions.

## Author

finance saber was created in 2026 by Otabek Davronov.

Built with [Cookiecutter](https://github.com/cookiecutter/cookiecutter) and the [audreyfeldroy/cookiecutter-pypackage](https://github.com/audreyfeldroy/cookiecutter-pypackage) project template.
