from django.db import migrations

# Substituição destrutiva de dev-stage (só usuários demo hoje) — sem
# remapeamento de usuários existentes entre grupo antigo e novo.
GRUPOS_REMOVIDOS = ['Gestores', 'Operadores']
GRUPOS_MANTIDOS = ['Administradores']
GRUPOS_NOVOS = ['Operador Produção', 'Operador Logística', 'Gestor Produção', 'Gestor Logística']


def migrar_grupos(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=GRUPOS_REMOVIDOS).delete()
    for nome in GRUPOS_MANTIDOS + GRUPOS_NOVOS:
        Group.objects.get_or_create(name=nome)


def reverter_grupos(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=GRUPOS_NOVOS).delete()
    for nome in GRUPOS_REMOVIDOS:
        Group.objects.get_or_create(name=nome)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_popular_grupos'),
    ]

    operations = [
        migrations.RunPython(migrar_grupos, reverter_grupos),
    ]
