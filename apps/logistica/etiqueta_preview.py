from apps.producao.etiqueta_preview import COMPRIMENTO, _fonte, desenhar_qr, png_bytes
from apps.producao.zpl import LARGURA_PAPEL, LOGO_U, LOGO_V, logo_bitmap

from .models import Endereco

# Réplica visual de templates_zpl/etiqueta_corredor.zpl, no mesmo referencial de
# leitura da etiqueta de palete (u horizontal, v vertical). Se o layout do .zpl
# mudar, atualizar as posições abaixo. Cada texto é (u, v da linha de base).
LETRA_ALTURA = 300
LETRA_U, LETRA_V = 480, 500
QR_MODULO_CORREDOR = 7  # ^BQN,2,7 no .zpl: a URL é maior que o código do palete


def renderizar_preview_corredor(corredor, url_mapa):
    """PNG da etiqueta de corredor em orientação de leitura (girada 90° em
    relação ao papel)."""
    from PIL import Image, ImageDraw

    codigo = Endereco.montar_codigo_corredor(corredor)
    img = Image.new('L', (COMPRIMENTO, LARGURA_PAPEL), 255)
    draw = ImageDraw.Draw(img)

    def texto(u, v, valor, altura):
        draw.text((u, v), valor, fill=0, font=_fonte(altura), anchor='ls')

    # Moldura, divisória do cabeçalho, logo e título
    draw.rectangle((20, 20, 1178, 778), outline=0, width=3)
    draw.rectangle((40, 182, 1158, 184), fill=0)
    img.paste(logo_bitmap().convert('L'), (LOGO_U, LOGO_V))
    texto(290, 121, 'IDENTIFICAÇÃO DE CORREDOR', 44)

    # Corredor: rótulo e letra em destaque
    texto(40, 240, 'CORREDOR', 54)
    texto(LETRA_U, LETRA_V, corredor, LETRA_ALTURA)

    # Rodapé: código à esquerda do QR Code
    texto(60, 600, 'CÓDIGO', 30)
    texto(60, 725, codigo, 110)
    desenhar_qr(draw, url_mapa, QR_MODULO_CORREDOR)

    return png_bytes(img)
