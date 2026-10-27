"""Rename the "Finance planner" role group to "Planner", keeping its members.

The roles were reorganised into Finance, Planner, Technologist and Manager
(Director); see users/roles.py. Their permissions are set after migrate by
sync_roles, so only the group name and memberships move here.
"""

from django.db import migrations

RENAMED = {"Finance planner": "Planner"}


def rename_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for old_name, new_name in RENAMED.items():
        old = Group.objects.filter(name=old_name).first()
        if old is None:
            continue
        new = Group.objects.filter(name=new_name).first()
        if new is None:
            old.name = new_name
            old.save(update_fields=["name"])
        else:  # both exist: move the members over, drop the old group
            new.user_set.add(*old.user_set.all())
            old.delete()


def rename_back(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for old_name, new_name in RENAMED.items():
        Group.objects.filter(name=new_name).exclude(
            pk__in=Group.objects.filter(name=old_name).values("pk")
        ).update(name=old_name)


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0004_daily_report"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(rename_groups, rename_back)]
