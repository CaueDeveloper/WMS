from django.db import migrations

GRUPOS = ['Administradores', 'Gestores', 'Operadores']


def criar_grupos(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    for nome in GRUPOS:
        Group.objects.get_or_create(name=nome)


def remover_grupos(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=GRUPOS).delete()


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(criar_grupos, remover_grupos),
    ]
