from django.db import migrations, models

STATUS_CHOICES = [
    ('FABRICADO', 'Fabricado na Produção'),
    ('TRANSFERENCIA_PRODUCAO', 'Conferido pela Produção'),
    ('APROVADO_PRODUCAO', 'Aprovado pela Produção'),
    ('TRANSFERENCIA_LOGISTICA', 'Conferido pela Logística'),
    ('APROVADO_LOGISTICA', 'Aprovado pela Logística'),
    ('ARMAZENADO', 'Armazenado no Estoque'),
    ('RETIRADO', 'Palete retirado do Estoque'),
    ('REPROVADO', 'Palete reprovado'),
]

# FINALIZADO nunca foi produzido por nenhum fluxo; se existir, volta a ARMAZENADO.
RENOMEAR = {
    'CRIADO': 'FABRICADO',
    'EM_TRANSFERENCIA': 'TRANSFERENCIA_PRODUCAO',
    'CONFERIDO': 'APROVADO_PRODUCAO',
    'EM_CONFERENCIA_LOGISTICA': 'TRANSFERENCIA_LOGISTICA',
    'CONFERIDO_LOGISTICA': 'APROVADO_LOGISTICA',
    'FINALIZADO': 'ARMAZENADO',
}


def _aplicar(apps, mapa):
    for nome in ('Palete', 'PaleteHistorico'):
        modelo = apps.get_model('producao', nome)
        for antigo, novo in mapa.items():
            modelo.objects.filter(estado=antigo).update(estado=novo)


def para_novos(apps, schema_editor):
    _aplicar(apps, RENOMEAR)


def para_antigos(apps, schema_editor):
    _aplicar(apps, {novo: antigo for antigo, novo in RENOMEAR.items() if antigo != 'FINALIZADO'})


class Migration(migrations.Migration):

    dependencies = [
        ('producao', '0016_palete_data_vencimento'),
    ]

    operations = [
        migrations.AlterField(
            model_name='palete', name='estado',
            field=models.CharField(choices=STATUS_CHOICES, default='FABRICADO', max_length=25),
        ),
        migrations.AlterField(
            model_name='paletehistorico', name='estado',
            field=models.CharField(choices=STATUS_CHOICES, max_length=25),
        ),
        migrations.RunPython(para_novos, para_antigos),
    ]
