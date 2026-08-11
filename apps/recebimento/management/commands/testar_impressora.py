from django.core.management.base import BaseCommand
from django.conf import settings

from apps.recebimento.models import Palete
from apps.recebimento.zpl import enviar_zpl, montar_zpl_etiqueta


class Command(BaseCommand):
    help = 'Testa conexão e envio de ZPL para a impressora Zebra'

    def add_arguments(self, parser):
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
        parser.add_argument(
            '--palete',
            action='store_true',
            help='Envia uma etiqueta de palete de exemplo (layout real) em vez do teste genérico.',
        )

    def handle(self, *args, **options):
        host = options['host']
        port = options['port']

        if options['palete']:
            palete_exemplo = Palete(
                barcode_palete='PAL20260803999999',
                sku='SKU-EXEMPLO',
                descricao='Café Torrado e Moído 1kg',
                lote='202608031234',
                peso_kg='60.500',
            )
            zpl = montar_zpl_etiqueta(palete_exemplo)
        else:
            zpl = (
                '^XA\n'
                '^CF0,50\n'
                '^FO50,50^FDTESTE ZPL^FS\n'
                '^FO50,150^FDConexão OK!^FS\n'
                '^XZ\n'
            )

        self.stdout.write(
            self.style.WARNING(f'Enviando etiqueta de teste para {host}:{port}...')
        )
        enviar_zpl(zpl, host=host, port=port)
        self.stdout.write(
            self.style.SUCCESS('Comando enviado. Verifique a impressora.')
        )
