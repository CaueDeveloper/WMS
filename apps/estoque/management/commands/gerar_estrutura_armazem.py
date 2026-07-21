from django.core.management.base import BaseCommand

from apps.estoque.models import Endereco

CORREDORES = range(1, 21)
ANDARES = ['A', 'B', 'C']


class Command(BaseCommand):
    help = (
        'Popula a matriz fixa de endereços do armazém: 20 corredores x 3 andares '
        '(60 posições, código "CC-A", capacidade=21).'
    )

    def handle(self, *args, **options):
        criados = 0
        existentes = 0

        for corredor in CORREDORES:
            for andar in ANDARES:
                codigo = f'{corredor:02d}-{andar}'
                _, criado = Endereco.objects.get_or_create(
                    codigo=codigo,
                    defaults={
                        'corredor': corredor,
                        'andar': andar,
                        'capacidade': 21,
                        'ocupacao_atual': 0,
                    },
                )
                if criado:
                    criados += 1
                else:
                    existentes += 1

        self.stdout.write(self.style.SUCCESS(
            f'Estrutura de armazém: {criados} endereço(s) criado(s), '
            f'{existentes} já existente(s). Total: {Endereco.objects.count()}.'
        ))
