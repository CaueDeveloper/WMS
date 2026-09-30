from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.estoque.models import Endereco
from apps.estoque.zpl import enviar_zpl, montar_zpl_etiqueta_endereco


class Command(BaseCommand):
    help = 'Imprime a etiqueta de código de barras de um endereço (mapa de estoque)'

    def add_arguments(self, parser):
        parser.add_argument(
            'codigo_endereco',
            type=str,
            help='Código do endereço a imprimir, ex.: 001-02-A-01-01. '
            'Não precisa existir no banco — aceita qualquer texto para teste.',
        )
        parser.add_argument(
            '--host',
            type=str,
            default=settings.IMPRESSORA_ZPL_HOST,
            help=f'IP da impressora (padrão: {settings.IMPRESSORA_ZPL_HOST})',
        )
        parser.add_argument(
            '--port',
            type=int,
            default=settings.IMPRESSORA_ZPL_PORT,
            help=f'Porta da impressora (padrão: {settings.IMPRESSORA_ZPL_PORT})',
        )

    def handle(self, *args, **options):
        codigo_endereco = options['codigo_endereco']
        host = options['host']
        port = options['port']

        endereco = Endereco.objects.filter(codigo=codigo_endereco).first()
        if endereco is None:
            self.stdout.write(self.style.WARNING(
                f'Endereço "{codigo_endereco}" não encontrado no banco — imprimindo etiqueta de '
                'teste mesmo assim (útil antes de rodar gerar_estrutura_armazem).'
            ))
            endereco = Endereco(codigo=codigo_endereco)

        if not host or not port:
            raise CommandError(
                'IMPRESSORA_ZPL_HOST/IMPRESSORA_ZPL_PORT não configurados — defina no .env ou passe --host/--port.'
            )

        zpl = montar_zpl_etiqueta_endereco(endereco)
        self.stdout.write(self.style.WARNING(f'Enviando etiqueta de "{endereco.codigo}" para {host}:{port}...'))
        enviar_zpl(zpl, host=host, port=port)
        self.stdout.write(self.style.SUCCESS('Comando enviado. Verifique a impressora.'))
