from django.db import migrations


class Migration(migrations.Migration):
    """A ocupação passa a ser calculada a partir da tabela de paletes
    (Endereco.objects.com_ocupacao()); o contador guardado deixa de existir."""

    dependencies = [
        ('estoque', '0013_remodelar_enderecos'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='endereco',
            name='ocupacao_atual',
        ),
    ]
