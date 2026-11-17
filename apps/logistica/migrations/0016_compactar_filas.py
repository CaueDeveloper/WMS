from django.db import migrations


def compactar_filas(apps, schema_editor):
    """Encosta os paletes armazenados na guarda 01 de cada corredor/andar (esteira
    de fluxo: a frente é a saída). Mesma regra de `services.compactar_fila`,
    aplicada uma vez ao estoque que já tinha vagas abertas na frente."""
    Endereco = apps.get_model('logistica', 'Endereco')
    Palete = apps.get_model('producao', 'Palete')
    filas = (
        Palete.objects.filter(estado='ARMAZENADO', endereco__isnull=False)
        .values_list('endereco__corredor', 'endereco__andar').distinct()
    )
    for corredor, andar in set(filas):
        enderecos = list(Endereco.objects.filter(corredor=corredor, andar=andar, ativo=True).order_by('posicao'))
        paletes = list(
            Palete.objects.filter(estado='ARMAZENADO', endereco__corredor=corredor, endereco__andar=andar)
            .order_by('endereco__posicao', 'pk')
        )
        movidos = []
        for palete, endereco in zip(paletes, enderecos):
            if palete.endereco_id != endereco.pk:
                palete.endereco_id = endereco.pk
                movidos.append(palete)
        if movidos:
            # Em duas etapas: o banco confere "um palete por endereço" linha a linha,
            # então primeiro libera os endereços de quem vai se mover.
            Palete.objects.filter(pk__in=[p.pk for p in movidos]).update(endereco=None)
            Palete.objects.bulk_update(movidos, ['endereco'])


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0015_criar_produto_pa000017'),
        ('producao', '0020_unidade'),
    ]

    operations = [
        migrations.RunPython(compactar_filas, migrations.RunPython.noop),
    ]
