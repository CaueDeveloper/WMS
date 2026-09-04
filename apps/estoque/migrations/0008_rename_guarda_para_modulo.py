# Generated manually — rename puro de coluna.
#
# Substitui a antiga 0008_endereco_capacidade_unica.py (nunca commitada, nunca
# aplicada neste ambiente). Aquela migration fazia um reset destrutivo
# (apagava todo Endereco) porque mudava semântica de verdade: removia
# `lado`, capacidade 21->1, faixa de guarda 1-20->1-21, codigo de 5 para 4
# segmentos. Nenhuma dessas mudanças é necessária agora: depois de 0001-0007,
# `guarda` já tem validators=[1,20] (herdado de `corredor` em 0001_initial,
# nunca alterado) e `capacidade` já tem default=21 (idem). A única mudança
# real que falta é o nome do campo. RenameField preserva os dados.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('estoque', '0007_recompute_codigo_lado1'),
    ]

    operations = [
        migrations.RenameField(
            model_name='endereco',
            old_name='guarda',
            new_name='modulo',
        ),
    ]
