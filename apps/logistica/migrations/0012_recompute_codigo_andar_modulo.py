# Generated manually on 2026-09-01

from django.db import migrations, models


def recomputar_codigos(apps, schema_editor):
    """Recalcula `codigo` dos endereços existentes para o novo formato curto
    'Andar-Módulo' (ex.: 'A-04'), abandonando o formato de 5 segmentos
    ('001-01-A-04-01'). Não usa Endereco.montar_codigo() de propósito: o
    modelo histórico do apps.get_model() não carrega staticmethods, e uma
    data migration deve ficar congelada independente de mudanças futuras no
    formatter (mesmo padrão da migration 0007)."""
    Endereco = apps.get_model('logistica', 'Endereco')
    for endereco in Endereco.objects.all():
        endereco.codigo = f'{endereco.andar}-{endereco.modulo:02d}'
        endereco.save(update_fields=['codigo'])


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0011_criar_produto_sobras'),
    ]

    operations = [
        migrations.RunPython(recomputar_codigos, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='endereco',
            name='codigo',
            field=models.CharField(max_length=4, unique=True),
        ),
    ]
