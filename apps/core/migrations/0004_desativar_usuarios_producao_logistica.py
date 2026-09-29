from django.conf import settings
from django.db import migrations

GRUPOS_INATIVOS_POR_PADRAO = [
    'Operador Produção', 'Gestor Produção', 'Operador Logística', 'Gestor Logística',
]


def desativar_usuarios(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL)
    User.objects.filter(
        is_superuser=False, groups__name__in=GRUPOS_INATIVOS_POR_PADRAO,
    ).distinct().update(is_active=False)


def reativar_usuarios(apps, schema_editor):
    # Irreversível de forma exata (não sabemos quem já estava inativo antes)
    # — reverter apenas reativa todo mundo do grupo, comportamento anterior.
    User = apps.get_model(settings.AUTH_USER_MODEL)
    User.objects.filter(
        is_superuser=False, groups__name__in=GRUPOS_INATIVOS_POR_PADRAO,
    ).distinct().update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_initial'),
    ]

    operations = [
        migrations.RunPython(desativar_usuarios, reativar_usuarios),
    ]
