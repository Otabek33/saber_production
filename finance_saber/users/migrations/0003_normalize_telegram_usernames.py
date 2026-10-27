"""Store Telegram usernames without the @, as Telegram itself reports them."""

from django.db import migrations


def strip_at(apps, schema_editor):
    User = apps.get_model("users", "User")
    for user in User.objects.filter(telegram_username__startswith="@"):
        user.telegram_username = user.telegram_username.strip().lstrip("@")
        user.save(update_fields=["telegram_username"])


class Migration(migrations.Migration):
    dependencies = [("users", "0002_telegram_and_reset_codes")]

    operations = [migrations.RunPython(strip_at, migrations.RunPython.noop)]
