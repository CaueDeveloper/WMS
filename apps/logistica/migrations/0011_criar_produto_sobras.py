from django.db import migrations


def criar_produto_sobras(apps, schema_editor):
    Produto = apps.get_model('logistica', 'Produto')
    Produto.objects.get_or_create(
        sku='SOBRAS',
        defaults={'descricao': 'Sobras', 'ativo': True, 'peso_variavel': True},
    )


def remover_produto_sobras(apps, schema_editor):
    Produto = apps.get_model('logistica', 'Produto')
    Produto.objects.filter(sku='SOBRAS').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0010_produto_peso_variavel'),
    ]

    operations = [
        migrations.RunPython(criar_produto_sobras, remover_produto_sobras),
    ]
