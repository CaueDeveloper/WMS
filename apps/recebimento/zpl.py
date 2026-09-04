import logging
import socket
import threading
from pathlib import Path

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Layout desenhado visualmente no ZebraDesigner e exportado como .zpl — os
# placeholders [[...]] ocupam o lugar dos dados de exemplo nos campos de
# texto/barcode do template. Ajustar o layout (posição, fonte, dimensões da
# etiqueta) é feito reabrindo este arquivo no ZebraDesigner, não em código.
CAMINHO_TEMPLATE_PALETE = Path(__file__).parent / 'templates_zpl' / 'etiqueta_palete.zpl'


def montar_zpl_etiqueta(palete):
    # Formatado manualmente (DD/MM/AAAA HH:MM) em vez de django.utils.formats:
    # LANGUAGE_CODE do projeto é 'en-us', mas todo o texto da aplicação é pt-BR.
    impresso_em = timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')
    data_impressao, hora_impressao = impresso_em.split(' ')
    template = CAMINHO_TEMPLATE_PALETE.read_text(encoding='utf-8')
    return (
        template
        .replace('[[BARCODE]]', palete.barcode_palete)
        .replace('[[LOTE]]', palete.lote)
        .replace('[[PESO]]', f'{palete.peso_kg:.3f}')
        .replace('[[DATA]]', data_impressao)
        .replace('[[HORA]]', hora_impressao)
    )


def montar_zpl_etiquetas(paletes):
    """Concatena um job ZPL completo (^XA...^XZ) por palete — cada um já é
    autocontido, então concatenar N deles forma um único payload válido para
    envio em uma única conexão de socket."""
    return ''.join(montar_zpl_etiqueta(p) for p in paletes)


def enviar_zpl(zpl, host=None, port=None, timeout=5):
    host = host or settings.IMPRESSORA_ZPL_HOST
    port = port or settings.IMPRESSORA_ZPL_PORT
    dados = zpl.encode('utf-8') if isinstance(zpl, str) else zpl
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(dados)
    except OSError:
        logger.exception('Falha ao enviar etiqueta ZPL para %s:%s', host, port)


def disparar_impressao_async(paletes):
    """Envia a(s) etiqueta(s) em uma thread separada para não travar a
    requisição HTTP. `paletes` é sempre uma lista/iterável — monta um job ZPL
    completo por palete e envia tudo em uma única conexão de socket."""
    zpl = montar_zpl_etiquetas(paletes)
    thread = threading.Thread(target=enviar_zpl, args=(zpl,), daemon=True)
    thread.start()
    return thread
