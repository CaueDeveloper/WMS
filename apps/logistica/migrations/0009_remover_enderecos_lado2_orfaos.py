from django.db import migrations


def remover_enderecos_lado2_orfaos(apps, schema_editor):
    """Lado 01/02 deixaram de ser duas grades físicas independentes — passam a ser a
    mesma grade de endereços, vista por duas telas diferentes (Put-away e Mapa de
    Estoque). Remove os endereços do Lado 02 criados enquanto essa distinção ainda
    existia, desde que nenhum palete aponte para eles (proteção defensiva: nenhum
    deveria, já que a regra que permitia armazenar lá foi adicionada e revertida na
    mesma sessão)."""
    Endereco = apps.get_model('logistica', 'Endereco')
    Endereco.objects.filter(lado=2, palete__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0008_rename_guarda_para_modulo'),
        ('producao', '0012_paletehistorico'),
    ]

    operations = [
        migrations.RunPython(remover_enderecos_lado2_orfaos, migrations.RunPython.noop),
    ]
