import django.db.models.deletion
from django.db import migrations, models


def copiar_notas(apps, schema_editor):
    """Cada romaneio antigo tinha no máximo uma nota: vira um RomaneioNota."""
    Romaneio = apps.get_model('romaneio', 'Romaneio')
    RomaneioNota = apps.get_model('romaneio', 'RomaneioNota')
    for r in Romaneio.objects.exclude(nota_docentry__isnull=True):
        nota = RomaneioNota.objects.create(romaneio=r, docentry=r.nota_docentry, docnum=r.nota_docnum or 0)
        r.itens.update(nota=nota)


class Migration(migrations.Migration):

    dependencies = [
        ('romaneio', '0010_posicao_do_item'),
    ]

    operations = [
        migrations.CreateModel(
            name='RomaneioNota',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('docentry', models.PositiveIntegerField(unique=True)),
                ('docnum', models.PositiveIntegerField(verbose_name='nota fiscal')),
                ('romaneio', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notas', to='romaneio.romaneio')),
            ],
            options={'ordering': ['docnum']},
        ),
        migrations.AddField(
            model_name='romaneioitem',
            name='nota',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='itens', to='romaneio.romaneionota'),
        ),
        migrations.RunPython(copiar_notas, migrations.RunPython.noop),
    ]
