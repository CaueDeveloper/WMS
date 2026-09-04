from django.core.management.base import BaseCommand

from apps.estoque.models import Endereco

MODULOS = range(1, 21)
ANDARES = ['A', 'B', 'C']


class Command(BaseCommand):
    help = (
        'Popula a matriz fixa de endereços do armazém: 20 módulos x 3 andares '
        '(60 posições, código "A-MM", capacidade=21).'
    )

    def handle(self, *args, **options):
        criados = 0
        existentes = 0

        for modulo in MODULOS:
            for andar in ANDARES:
                codigo = Endereco.montar_codigo(lado=Endereco.LADO_ENTRADA, andar=andar, modulo=modulo)
                _, criado = Endereco.objects.get_or_create(
                    codigo=codigo,
                    defaults={
                        'lado': Endereco.LADO_ENTRADA,
                        'modulo': modulo,
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
