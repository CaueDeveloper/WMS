from django.db import migrations


class Migration(migrations.Migration):
    """Conferência cega: o romaneio deixa de guardar cliente e datas da nota."""

    dependencies = [
        ('romaneio', '0011_romaneio_nota'),
    ]

    operations = [
        migrations.RemoveField(model_name='romaneio', name=nome)
        for nome in ('nota_docentry', 'nota_docnum', 'cliente_codigo', 'cliente_nome',
                     'data_lancamento', 'data_vencimento', 'valor_total')
    ]
