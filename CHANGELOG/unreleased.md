# Unreleased

These are the changes that will go out in the next release.

## Added

finance saber started out as a project generated from [Cookiecutter PyPackage](https://github.com/audreyfeldroy/cookiecutter-pypackage) containing:

- Initial scaffold for finance saber.
- `src/finance_saber/` package with CLI (Typer + Rich), py.typed marker
- Tests with pytest, coverage across Python 3.12/3.13/3.14
- CI via GitHub Actions: lint (Ruff), type check (ty), test matrix, coverage reporting
- Security scanning: CodeQL analysis for public repositories, Dependabot, and a Zizmor workflow audit
- Docs site with Zensical + mkdocstrings and a GitHub Pages deployment workflow
- Trusted publishing to PyPI with OIDC and build provenance attestation
- `justfile` with dev commands: qa, test, type-check, docs-serve, release
- Issue templates, PR template, contributing guide, code of conduct, security policy
- MIT license, .editorconfig, .gitignore
- Server deployment kit in `deploy/`: gunicorn config, systemd units (app + daily Telegram report timer), nginx site with HTTPS, and `deploy.sh` for releases
- `deploy/setup_server.sh`: first install on a fresh Ubuntu/Debian server in one command (PostgreSQL, HTTPS certificate, services, nginx); safe to re-run, and re-running with a new `DOMAIN` moves the site
- `uv.lock` so servers install the exact tested dependency versions
- Cash desk › Upload Excel: one upload of the finance workbook fills Касса БОН, Инкасса & Розница and Банк; check first, then import only new rows, with conflicts and new counterparties shown before anything is saved
- Справочник and Контрагенты: «Скачать Excel» downloads; «Загрузить из Excel» and `export_handbooks` / `load_handbooks` to fill a new server's empty database with the same handbooks
- Production › Калькуляция: each production keeps its own recipe (norms per 1000 l / per unit, production defect, allowed loss) and brewed quantity, like the calculation sheet; the latest production updates the product's recipe
- Administration section for the superuser: users (create with a role and password, edit, block, set a password) and a roles-and-permissions overview
- Roles Finance, Planner, Technologist and Operator (sales entry); Manager is shown as Director
- Roles and permissions › Edit access: the superuser sets a role's access per section (no access / views / works); changes are kept across updates, logged, and can be reset to the standard
- The handbooks are kept in the project (`cashbook/seed/`, written by `export_handbooks --seed`): «Файл из проекта» downloads them, and «Загрузить файлы проекта» fills a new server in one click
- Рецептуры и нормы: «Скачать Excel» (products, materials, recipes, other product names) and «Загрузить из Excel»; the recipes are kept in the project (`production/seed/recipes.xlsx`, written by `export_recipes --seed`) and load into a new server in one click
- Сырьё на складе: «Скачать Excel» (materials with their stock) and «Загрузить из Excel»; the project's `production/seed/materials.xlsx` (`export_materials --seed`, `load_materials`) fills a new server's materials, and optionally their stock as opening balances on a chosen date, only for materials with no stock movements yet

## Changed

- Production settings require `DATABASE_URL` instead of silently falling back to SQLite
- `/health/` is exempt from the HTTPS redirect so local uptime checks work
- Recipe norms accept a decimal comma with any number of digits ("0,0355" was read as 355) and fractions like 1/12
- Re-saving a past production day no longer recalculates it with the current recipe
- The Finance planner role became Planner (plans only); cash desks, bank and sales moved to the new Finance role

### Contributors

[@Otabek33](https://github.com/Otabek33) (Otabek Davronov) created finance saber.
