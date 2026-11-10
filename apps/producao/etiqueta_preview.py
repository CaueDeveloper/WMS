import io

from .zpl import LARGURA_PAPEL, LOGO_U, LOGO_V, campos_etiqueta, logo_bitmap

# Réplica visual de templates_zpl/etiqueta_palete.zpl, desenhada no referencial
# de leitura (u horizontal, v vertical; etiqueta 1199 x 799 dots). Se o layout do
# .zpl mudar, atualizar as posições abaixo. Cada texto é (u, v da linha de base).
COMPRIMENTO = 1199
BARCODE_EXEMPLO = 'PLT-XXXXXXXXXXXX'

# QR Code (^BQN,2,10, correção L): modelo 2, versão 1 (21 módulos = 210 dots), módulo
# de 10 dots, origem (^FO) em (x=38, y=720) no papel, à direita do vencimento no rodapé.
# ^BQ não rotaciona (diferente de ^BC/^A0R): no papel o QR sai "normal".
QR_X, QR_Y, QR_MODULO = 38, 720, 10

GRADE_V0, GRADE_V1 = 365, 535
GRADE_U = (40, 440, 690, 960, 1159)

_FONTES = (
    'arial.ttf', 'Arial.ttf', 'DejaVuSans.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    'C:/Windows/Fonts/arial.ttf',
)


def _fonte(altura_zpl):
    from PIL import ImageFont

    tamanho = round(altura_zpl * 0.95)
    for nome in _FONTES:
        try:
            return ImageFont.truetype(nome, tamanho)
        except OSError:
            continue
    return ImageFont.load_default(tamanho)


def _matriz_qr(texto):
    """Matriz do QR (True = módulo escuro), sem a zona de silêncio."""
    import segno

    return [list(linha) for linha in segno.make(texto, error='l', micro=False, boost_error=False).matrix]


def renderizar_preview(palete):
    """PNG da etiqueta de palete em orientação de leitura (girada 90° em relação
    ao papel), a partir de um Palete (salvo ou não)."""
    from PIL import Image, ImageDraw

    c = campos_etiqueta(palete)
    img = Image.new('L', (COMPRIMENTO, LARGURA_PAPEL), 255)
    draw = ImageDraw.Draw(img)

    def texto(u, v, valor, altura):
        draw.text((u, v), valor, fill=0, font=_fonte(altura), anchor='ls')

    def caixa(u0, v0, u1, v1, espessura=3):
        draw.rectangle((u0, v0, u1 - 1, v1 - 1), outline=0, width=espessura)

    # Moldura, divisória do cabeçalho, logo e título
    caixa(20, 20, 1179, 779)
    draw.rectangle((40, 182, 1158, 184), fill=0)
    img.paste(logo_bitmap().convert('L'), (LOGO_U, LOGO_V))
    texto(290, 121, 'IDENTIFICAÇÃO DE PALETE', 44)

    # Produto
    texto(40, 232, c['desc1'], 44)
    texto(40, 282, c['desc2'], 44)
    texto(40, 340, f"SKU: {c['sku']}", 54)

    # Grade: Lote | Peso | Fabricação | Hora (bordas compartilhadas, como no ZPL)
    celulas = (
        ('LOTE', c['lote'], c['lote_altura']),
        ('PESO (KG)', c['peso'], 40),
        ('FABRICAÇÃO', c['data'], 40),
        ('HORA', c['hora'], 40),
    )
    for i, (rotulo, valor, altura) in enumerate(celulas):
        u0, u1 = GRADE_U[i], GRADE_U[i + 1] + (3 if i < len(celulas) - 1 else 0)
        caixa(u0, GRADE_V0, u1, GRADE_V1)
        texto(u0 + 16, GRADE_V0 + 40, rotulo, 24)
        texto(u0 + 16, GRADE_V0 + 130, valor, altura)

    # Vencimento em destaque, à esquerda do QR (baseline em v = 799 - x do ZPL)
    texto(60, 600, 'VENCIMENTO', 30)
    texto(60, 725, c['vencimento'], 110)

    desenhar_qr(draw, c['barcode'])
    return png_bytes(img)


def desenhar_qr(draw, texto, modulo=QR_MODULO):
    """QR Code do rodapé: módulo (linha, coluna) -> papel (x, y) -> leitura (u, v) = (y, 799 - x)."""
    for linha, modulos in enumerate(_matriz_qr(texto)):
        for coluna, escuro in enumerate(modulos):
            if not escuro:
                continue
            x0 = QR_X + coluna * modulo
            y0 = QR_Y + linha * modulo
            draw.rectangle(
                (y0, LARGURA_PAPEL - x0 - modulo, y0 + modulo - 1, LARGURA_PAPEL - x0 - 1), fill=0,
            )


def png_bytes(img):
    saida = io.BytesIO()
    img.save(saida, format='PNG')
    return saida.getvalue()
