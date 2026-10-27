"""Split settings.

- ``base``: shared by every environment.
- ``local``: development on your machine (default for ``manage.py``).
- ``production``: deployed servers (default for WSGI/ASGI).
- ``test``: the pytest suite.

Pick one with the ``DJANGO_SETTINGS_MODULE`` environment variable.
"""
