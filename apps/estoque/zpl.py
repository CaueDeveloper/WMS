from pathlib import Path

from apps.recebimento.zpl import enviar_zpl  # noqa: F401

# Layout desenhado visualmente no ZebraDesigner e exportado como .zpl — o
# placeholder [[CODIGO_ENDERECO]] ocupa o lugar do dado de exemplo tanto no
# campo de barcode quanto no texto legível abaixo dele. Ajustar o layout
# (posição, fonte, dimensões da etiqueta) é feito reabrindo este arquivo no
# ZebraDesigner, não em código.
CAMINHO_TEMPLATE_ENDERECO = Path(__file__).parent / 'templates_zpl' / 'etiqueta_endereco.zpl'


def montar_zpl_etiqueta_endereco(endereco):
    template = CAMINHO_TEMPLATE_ENDERECO.read_text(encoding='utf-8')
    return template.replace('[[CODIGO_ENDERECO]]', endereco.codigo)
