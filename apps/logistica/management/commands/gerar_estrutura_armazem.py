from django.core.management.base import BaseCommand

from apps.logistica.models import Endereco


class Command(BaseCommand):
    help = (
        'Popula a matriz fixa de endereços do armazém: 20 corredores (A–T) x 3 andares (1–3) '
        'x 21 posições (1260 endereços, código "001-02-A-01-01", capacidade=1).'
    )

    def handle(self, *args, **options):
        existentes = set(Endereco.objects.values_list('codigo', flat=True))
        novos = [
            Endereco(
                codigo=Endereco.montar_codigo(corredor, andar, posicao),
                corredor=corredor, andar=andar, posicao=posicao, capacidade=1,
            )
            for corredor in Endereco.CORREDORES
            for andar in Endereco.ANDARES
            for posicao in Endereco.POSICOES
            if Endereco.montar_codigo(corredor, andar, posicao) not in existentes
        ]
        Endereco.objects.bulk_create(novos)

        self.stdout.write(self.style.SUCCESS(
            f'Estrutura de armazém: {len(novos)} endereço(s) criado(s), '
            f'{len(existentes)} já existente(s). Total: {Endereco.objects.count()}.'
        ))
