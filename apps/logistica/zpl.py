import threading
from pathlib import Path

from apps.producao.zpl import enviar_zpl, logo_zpl

from .models import Endereco

# Layout desenhado visualmente no ZebraDesigner e exportado como .zpl — o
# placeholder [[CODIGO_ENDERECO]] ocupa o lugar do dado de exemplo tanto no
# campo de barcode quanto no texto legível abaixo dele. Ajustar o layout
# (posição, fonte, dimensões da etiqueta) é feito reabrindo este arquivo no
# ZebraDesigner, não em código.
CAMINHO_TEMPLATE_ENDERECO = Path(__file__).parent / 'templates_zpl' / 'etiqueta_endereco.zpl'

# Etiqueta de corredor: mesma moldura, logo e QR da etiqueta de palete; o
# preview em etiqueta_preview.py é a réplica visual deste arquivo.
CAMINHO_TEMPLATE_CORREDOR = Path(__file__).parent / 'templates_zpl' / 'etiqueta_corredor.zpl'


def montar_zpl_etiqueta_endereco(endereco):
    template = CAMINHO_TEMPLATE_ENDERECO.read_text(encoding='utf-8')
    return template.replace('[[CODIGO_ENDERECO]]', endereco.codigo)


def montar_zpl_etiqueta_corredor(corredor, url_mapa):
    """`corredor` é uma letra de Endereco.CORREDORES (já validada pelo chamador);
    `url_mapa` é o que o QR Code abre (Consulta de corredor)."""
    template = CAMINHO_TEMPLATE_CORREDOR.read_text(encoding='utf-8')
    return (
        template
        .replace('[[LOGO]]', logo_zpl())
        .replace('[[CODIGO_CORREDOR]]', Endereco.montar_codigo_corredor(corredor))
        .replace('[[URL_MAPA]]', url_mapa)
        .replace('[[CORREDOR]]', corredor)
    )


def montar_zpl_etiquetas_corredor(corredores_urls):
    """Um job ZPL completo (^XA...^XZ) por (corredor, url_mapa), concatenados em um único payload."""
    return ''.join(montar_zpl_etiqueta_corredor(c, url) for c, url in corredores_urls)


def disparar_impressao_corredores_async(corredores_urls):
    """Envia as etiquetas em uma thread separada para não travar a requisição HTTP."""
    zpl = montar_zpl_etiquetas_corredor(corredores_urls)
    thread = threading.Thread(target=enviar_zpl, args=(zpl,), daemon=True)
    thread.start()
    return thread
