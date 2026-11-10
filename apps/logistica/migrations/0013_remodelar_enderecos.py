# Generated manually on 2026-09-29

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models

CORREDORES = 'ABCDEFGHIJKLMNOPQRST'
ANDARES_ANTIGOS = 'ABC'
PREFIXO = '001-02'


def converter_enderecos(apps, schema_editor):
    """Cada endereço antigo (módulo 1–20 × andar A/B/C, capacidade 21) vira 21
    endereços de 1 palete (corredor A–T × andar 1–3 × posição 1–21). O módulo
    N vira o corredor da N-ésima letra e o andar A/B/C vira 1/2/3; os paletes
    que apontavam para o endereço antigo ocupam as posições 01, 02, ... em
    ordem de pk. Formato do código congelado aqui (data migration não usa
    Endereco.montar_codigo — mesmo padrão da migration 0007)."""
    Endereco = apps.get_model('logistica', 'Endereco')
    Palete = apps.get_model('producao', 'Palete')

    for antigo in list(Endereco.objects.order_by('modulo', 'andar_antigo', 'pk')):
        corredor = CORREDORES[antigo.modulo - 1]
        andar = ANDARES_ANTIGOS.index(antigo.andar_antigo) + 1
        paletes = list(Palete.objects.filter(endereco_id=antigo.pk).order_by('pk'))
        if len(paletes) > 21:
            raise RuntimeError(
                f'Endereço {antigo.codigo} tem {len(paletes)} paletes vinculados — máximo 21 por corredor/andar.'
            )

        posicoes = [antigo]
        for posicao in range(2, 22):
            posicoes.append(Endereco(
                codigo=f'{PREFIXO}-{corredor}-{andar:02d}-{posicao:02d}',
                corredor=corredor, andar=andar, posicao=posicao,
                capacidade=1, ocupacao_atual=0, ativo=antigo.ativo,
                modulo=antigo.modulo, lado=antigo.lado, andar_antigo=antigo.andar_antigo,
            ))

        antigo.codigo = f'{PREFIXO}-{corredor}-{andar:02d}-01'
        antigo.corredor, antigo.andar, antigo.posicao = corredor, andar, 1
        antigo.capacidade = 1
        antigo.ocupacao_atual = 0
        antigo.save()
        for posicao in posicoes[1:]:
            posicao.save()

        for indice, palete in enumerate(paletes):
            destino = posicoes[indice]
            Palete.objects.filter(pk=palete.pk).update(endereco_id=destino.pk)
            destino.ocupacao_atual = 1
            destino.save(update_fields=['ocupacao_atual'])


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0012_recompute_codigo_andar_modulo'),
        ('producao', '0013_palete_datas_rastreabilidade'),
    ]

    operations = [
        migrations.AlterField(
            model_name='endereco',
            name='codigo',
            field=models.CharField(max_length=14, unique=True),
        ),
        migrations.RenameField(model_name='endereco', old_name='andar', new_name='andar_antigo'),
        migrations.AddField(
            model_name='endereco', name='corredor',
            field=models.CharField(default='A', max_length=1),
        ),
        migrations.AddField(
            model_name='endereco', name='andar',
            field=models.IntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(3)]),
        ),
        migrations.AddField(
            model_name='endereco', name='posicao',
            field=models.IntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(21)]),
        ),
        migrations.RunPython(converter_enderecos, migrations.RunPython.noop),
        migrations.RemoveField(model_name='endereco', name='modulo'),
        migrations.RemoveField(model_name='endereco', name='lado'),
        migrations.RemoveField(model_name='endereco', name='andar_antigo'),
        migrations.AlterField(
            model_name='endereco', name='corredor',
            field=models.CharField(
                choices=[(c, c) for c in CORREDORES], max_length=1,
            ),
        ),
        migrations.AlterField(
            model_name='endereco', name='andar',
            field=models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(3)]),
        ),
        migrations.AlterField(
            model_name='endereco', name='posicao',
            field=models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(21)]),
        ),
        migrations.AlterField(model_name='endereco', name='capacidade', field=models.IntegerField(default=1)),
        migrations.AddConstraint(
            model_name='endereco',
            constraint=models.UniqueConstraint(
                fields=('corredor', 'andar', 'posicao'), name='endereco_corredor_andar_posicao_uniq',
            ),
        ),
    ]
