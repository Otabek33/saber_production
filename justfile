# Justfile for finance_saber

# Show available commands
list:
    @just --list

alias r := run
alias c := clean
alias d := docs-serve
alias t := test
alias tc := type-check

# Type check the project with ty
type-check:
    uv run --python=3.14 ty check .

# Type check with concise output (one diagnostic per line)
type-check-concise:
    uv run --python=3.14 ty check --output-format=concise .

# Type check in watch mode (rechecks on file changes)
type-check-watch:
    uv run --python=3.14 ty check --watch .

# Apply automatic formatting and lint fixes
fix:
    uv run --python=3.14 ruff format .
    uv run --python=3.14 ruff check . --fix

# Verify formatting, linting, types, and tests on Python 3.14 without modifying source files
check:
    uv run --python=3.14 ruff format --check .
    uv run --python=3.14 ruff check .
    uv run --python=3.14 ty check .
    uv run --python=3.14 python manage.py makemigrations --check --dry-run
    uv run --python=3.14 pytest

# Apply automatic fixes, then run the local quality gate
fix-and-check: fix check

# Compatibility alias for fix-and-check
qa: fix-and-check

# Run all the tests for all the supported Python versions
testall:
    uv run --python=3.12 pytest
    uv run --python=3.13 pytest
    uv run --python=3.14 pytest

# Run all the tests, but allow for arguments to be passed
test *ARGS:
    @echo "Running with arg: {{ARGS}}"
    uv run --python=3.14 pytest {{ARGS}}

# Run all the tests, but on failure, drop into the debugger
pdb *ARGS:
    @echo "Running with arg: {{ARGS}}"
    uv run --python=3.14 pytest --pdb --maxfail=10 {{ARGS}}

# Run tests with coverage across all supported Python versions
coverage:
    uv run --python=3.12 coverage run -m pytest
    uv run --python=3.13 coverage run -m pytest
    uv run --python=3.14 coverage run -m pytest
    uv run --python=3.14 coverage combine
    uv run --python=3.14 coverage report
    uv run --python=3.14 coverage html

# Serve docs locally with live reload
docs-serve:
    -lsof -ti :8000 | xargs kill
    uv run --group docs zensical serve

# Build docs (strict mode, fails on warnings)
docs-build:
    uv run --group docs zensical build --clean

# Run the development server (config.settings.local)
run *ARGS:
    uv run python manage.py runserver {{ARGS}}

# Run any manage.py command, e.g. `just manage createsuperuser`
manage *ARGS:
    uv run python manage.py {{ARGS}}

# Create new migrations
makemigrations *ARGS:
    uv run python manage.py makemigrations {{ARGS}}

# Apply migrations
migrate *ARGS:
    uv run python manage.py migrate {{ARGS}}

# Fail if models changed without a migration
migrations-check:
    uv run python manage.py makemigrations --check --dry-run

# Run Django's deployment checklist against the production settings (needs production env vars)
deploy-check:
    uv run python manage.py check --deploy --settings=config.settings.production

# Finalize release notes, tag, push, and create a GitHub release
release:
    uv run scripts/release.py

# Remove all build, test, coverage and Python artifacts
clean: clean-build clean-pyc clean-test

# Remove build artifacts
clean-build:
	rm -fr build/
	rm -fr dist/
	rm -fr .eggs/
	find . -name '*.egg-info' -exec rm -fr {} +
	find . -name '*.egg' -exec rm -f {} +

# Remove Python file artifacts
clean-pyc:
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -fr {} +

# Remove test and coverage artifacts
clean-test:
	rm -f .coverage
	rm -f .coverage.*
	rm -fr htmlcov/
	rm -fr .pytest_cache
