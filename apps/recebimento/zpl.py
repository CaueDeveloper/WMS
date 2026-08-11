import logging
import socket
import threading

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


LARGURA_ETIQUETA = 812  # 4" (largura física / ^PW) a 203dpi [ASSUMPTION]: etiqueta 4"x6" na Zebra ZT421
COMPRIMENTO_ETIQUETA = 1218  # 6" (comprimento / ^LL) a 203dpi


def montar_zpl_etiqueta(palete):
    largura = LARGURA_ETIQUETA
    comprimento = COMPRIMENTO_ETIQUETA
    # Formatado manualmente (DD/MM/AAAA HH:MM) em vez de django.utils.formats:
    # LANGUAGE_CODE do projeto é 'en-us', mas todo o texto da aplicação é pt-BR.
    impresso_em = timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')
    # Com ^FWR o texto passa a fluir no eixo Y (comprimento), então o bloco de
    # centralização (^FB) precisa usar `comprimento`, não `largura` — e as linhas
    # empilham ao longo de X (antes empilhavam em Y, quando o texto era horizontal).
    return (
        '^XA\n'
        f'^PW{largura}\n'
        f'^LL{comprimento}\n'
        '^FWR\n'  # gira todos os campos 90° (sentido horário)
        '^CF0,70\n'
        f'^FO70,0^FB{comprimento},1,0,C^FD{palete.sku}^FS\n'
        f'^FO160,0^FB{comprimento},1,0,C^FD{palete.lote}^FS\n'
        f'^FO250,0^FB{comprimento},1,0,C^FDPeso: {palete.peso_kg} kg^FS\n'
        f'^FO340,0^FB{comprimento},1,0,C^FD{impresso_em}^FS\n'
        f'^FO480,280^BY3\n'
        # ^FB não é aplicado ao campo de barcode: na prática a impressora ignora o
        # ^BC quando combinado com ^FB e imprime o dado como texto simples, em vez
        # de gerar as barras. O posicionamento em Y=280 centraliza manualmente.
        f'^BCR,130,N,N,N^FD{palete.barcode_palete}^FS\n'
        '^XZ\n'
    )


def enviar_zpl(zpl, host=None, port=None, timeout=5):
    host = host or settings.IMPRESSORA_ZPL_HOST
    port = port or settings.IMPRESSORA_ZPL_PORT
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(zpl.encode('utf-8'))
    except OSError:
        logger.exception('Falha ao enviar etiqueta ZPL para %s:%s', host, port)


def disparar_impressao_async(palete):
    """Envia a etiqueta em uma thread separada para não travar a requisição HTTP."""
    zpl = montar_zpl_etiqueta(palete)
    thread = threading.Thread(target=enviar_zpl, args=(zpl,), daemon=True)
    thread.start()
    return thread
