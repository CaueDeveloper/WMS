from django.db import migrations


def criar_produto_pa000017(apps, schema_editor):
    Produto = apps.get_model('logistica', 'Produto')
    Produto.objects.get_or_create(
        sku='PA000017',
        defaults={'descricao': 'CAPPUCCINO CANELA DO SITIO 24X200GR', 'ativo': True},
    )


def remover_produto_pa000017(apps, schema_editor):
    Produto = apps.get_model('logistica', 'Produto')
    Produto.objects.filter(sku='PA000017').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0014_remove_endereco_ocupacao_atual'),
    ]

    operations = [
        migrations.RunPython(criar_produto_pa000017, remover_produto_pa000017),
    ]
