import logging
import socket
import threading

from django.conf import settings

logger = logging.getLogger(__name__)


def montar_zpl_etiqueta(palete):
    return (
        '^XA\n'
        '^CF0,40\n'
        f'^FO50,30^FD{palete.sku}^FS\n'
        f'^FO50,80^FDLote: {palete.lote}^FS\n'
        f'^FO50,130^FDPeso: {palete.peso_kg} kg^FS\n'
        '^FO50,180^BY2\n'
        f'^BCN,80,Y,N,N\n^FD{palete.barcode_palete}^FS\n'
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
