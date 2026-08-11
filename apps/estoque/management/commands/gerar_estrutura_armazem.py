from django.core.management.base import BaseCommand

from apps.estoque.models import Endereco

GUARDAS = range(1, 21)
ANDARES = ['A', 'B', 'C']
LADOS = [1, 2]


class Command(BaseCommand):
    help = (
        'Popula a matriz fixa de endereços do armazém: 20 guardas x 3 andares x 2 lados '
        '(120 posições, código "001-LL-A-GG-01", capacidade=21).'
    )

    def handle(self, *args, **options):
        criados = 0
        existentes = 0

        for lado in LADOS:
            for guarda in GUARDAS:
                for andar in ANDARES:
                    codigo = Endereco.montar_codigo(lado=lado, andar=andar, guarda=guarda)
                    _, criado = Endereco.objects.get_or_create(
                        codigo=codigo,
                        defaults={
                            'guarda': guarda,
                            'andar': andar,
                            'lado': lado,
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
