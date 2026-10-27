from django.apps import AppConfig
from django.db.models.signals import post_migrate


def sync_role_groups(sender, using="default", **kwargs):
    """After ``migrate``: make sure every app's permissions exist, then (re)build the role groups."""
    from django.apps import apps
    from django.contrib.auth.management import create_permissions

    from .roles import sync_roles

    # Apps after this one in INSTALLED_APPS haven't had their permissions created yet.
    for app_config in apps.get_app_configs():
        create_permissions(app_config, verbosity=0, using=using)
    sync_roles()


class UsersConfig(AppConfig):
    name = "finance_saber.users"
    verbose_name = "Users"

    def ready(self):
        post_migrate.connect(sync_role_groups, sender=self, dispatch_uid="finance_saber.users.sync_role_groups")
