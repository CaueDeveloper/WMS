import functools
import logging
import socket
import textwrap
import threading
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.core.templatetags.core_extras import formatar_peso

logger = logging.getLogger(__name__)

# Layout desenhado visualmente no ZebraDesigner e exportado como .zpl — os
# placeholders [[...]] ocupam o lugar dos dados de exemplo nos campos de
# texto/barcode do template. Ajustar o layout (posição, fonte, dimensões da
# etiqueta) é feito reabrindo este arquivo no ZebraDesigner, não em código.
CAMINHO_TEMPLATE_PALETE = Path(__file__).parent / 'templates_zpl' / 'etiqueta_palete.zpl'


CAMINHO_LOGO = Path(__file__).resolve().parents[1] / 'core' / 'static' / 'core' / 'img' / 'familia-do-sitio.png'

# Referencial de leitura da etiqueta: u = horizontal (0..1199), v = vertical
# (0..799). No papel (799 de largura) o conteúdo sai girado 90°, então um ponto
# de leitura (u, v) vira (x, y) = (LARGURA_PAPEL - v, u) nos comandos ZPL.
LARGURA_PAPEL = 799
LOGO_U, LOGO_V, LOGO_ALTURA = 40, 40, 130
LOGO_LIMIAR = 150  # abaixo disso vira preto; o laranja das ondas some, as letras ficam

# Capacidade aproximada do campo Lote por tamanho de fonte: (altura, nº de caracteres).
_FONTES_LOTE = ((40, 16), (32, 21), (26, 26))
DESCRICAO_LARGURA = 42
DESCRICAO_LINHAS = 2


def escapar_zpl(texto):
    """Escapa para ^FD sob ^FH\\ e ^CI28 (UTF-8): bytes fora do ASCII seguro e os
    caracteres de controle do ZPL (^ ~ \\) viram \\XX."""
    saida = []
    for byte in texto.encode('utf-8'):
        if 32 <= byte < 127 and chr(byte) not in '^~\\':
            saida.append(chr(byte))
        else:
            saida.append(f'\\{byte:02X}')
    return ''.join(saida)


def quebrar_descricao(descricao):
    """Descrição em até DESCRICAO_LINHAS linhas (a última termina em '...' se cortada)."""
    linhas = textwrap.wrap(
        descricao.strip(), width=DESCRICAO_LARGURA, max_lines=DESCRICAO_LINHAS, placeholder='...',
    )
    return linhas + [''] * (DESCRICAO_LINHAS - len(linhas))


def ajustar_lote(lote):
    """(texto, altura da fonte) do campo Lote: reduz a fonte e, no limite, trunca."""
    for altura, capacidade in _FONTES_LOTE:
        if len(lote) <= capacidade:
            return lote, altura
    altura, capacidade = _FONTES_LOTE[-1]
    return lote[:capacidade - 3] + '...', altura


def campos_etiqueta(palete):
    """Textos (sem escape) que compõem a etiqueta; usados no ZPL e na pré-visualização."""
    # Formatado manualmente (DD/MM/AAAA HH:MM) em vez de django.utils.formats:
    # mantém a formatação independente do idioma configurado.
    impresso_em = timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')
    data_impressao, hora_impressao = impresso_em.split(' ')
    # "Fabricação" é o campo da etiqueta; paletes antigos (sem data_fabricacao)
    # caem na data de impressão.
    data_fabricacao = (
        palete.data_fabricacao.strftime('%d/%m/%Y') if palete.data_fabricacao else data_impressao
    )
    vencimento = palete.data_vencimento.strftime('%d/%m/%Y') if palete.data_vencimento else '-'
    lote, lote_altura = ajustar_lote(palete.lote)
    desc1, desc2 = quebrar_descricao(palete.descricao)
    return {
        'barcode': palete.barcode_palete,
        'lote': lote,
        'lote_altura': lote_altura,
        'peso': formatar_peso(palete.peso_kg),
        'data': data_fabricacao,
        'vencimento': vencimento,
        'hora': hora_impressao,
        'sku': palete.sku,
        'desc1': desc1,
        'desc2': desc2,
    }


@functools.lru_cache(maxsize=1)
def logo_bitmap():
    """Logo em 1 bit (modo '1', preto = 0) na orientação de leitura, já na altura final."""
    from PIL import Image, ImageFilter

    original = Image.open(CAMINHO_LOGO).convert('RGBA')
    # Corta a margem transparente (e a sombra quase invisível) para a logo ocupar a altura toda
    original = original.crop(original.getchannel('A').point(lambda a: 255 if a > 100 else 0).getbbox())
    fundo = Image.new('RGBA', original.size, (255, 255, 255, 255))
    fundo.alpha_composite(original)
    cinza = fundo.convert('L')
    largura = round(cinza.width * LOGO_ALTURA / cinza.height)
    cinza = cinza.resize((largura, LOGO_ALTURA), Image.Resampling.LANCZOS)
    preto_e_branco = cinza.point(lambda p: 255 if p >= LOGO_LIMIAR else 0)
    # Mediana 3x3 remove os pontos soltos que a sombra da logo deixa ao virar 1 bit
    return preto_e_branco.filter(ImageFilter.MedianFilter(3)).convert('1')


def logo_zpl():
    """Comando ^GFA da logo posicionada em (LOGO_U, LOGO_V) do referencial de leitura."""
    from PIL import Image, ImageOps

    papel = logo_bitmap().transpose(Image.Transpose.ROTATE_270)  # leitura -> papel
    dados = ImageOps.invert(papel.convert('L')).convert('1').tobytes()  # preto = bit 1
    bytes_linha = (papel.width + 7) // 8
    x = LARGURA_PAPEL - (LOGO_V + LOGO_ALTURA)
    return f'^FO{x},{LOGO_U}^GFA,{len(dados)},{len(dados)},{bytes_linha},{dados.hex().upper()}^FS'


def montar_zpl_etiqueta(palete):
    c = campos_etiqueta(palete)
    template = CAMINHO_TEMPLATE_PALETE.read_text(encoding='utf-8')
    return (
        template
        .replace('[[LOGO]]', logo_zpl())
        .replace('[[BARCODE]]', escapar_zpl(c['barcode']))
        .replace('[[LOTE_ALTURA]]', str(c['lote_altura']))
        .replace('[[LOTE]]', escapar_zpl(c['lote']))
        .replace('[[PESO]]', escapar_zpl(c['peso']))
        .replace('[[DATA]]', c['data'])
        .replace('[[VENCIMENTO]]', c['vencimento'])
        .replace('[[HORA]]', c['hora'])
        .replace('[[SKU]]', escapar_zpl(c['sku']))
        .replace('[[DESC1]]', escapar_zpl(c['desc1']))
        .replace('[[DESC2]]', escapar_zpl(c['desc2']))
    )


def montar_zpl_etiquetas(paletes):
    """Concatena um job ZPL completo (^XA...^XZ) por palete — cada um já é
    autocontido, então concatenar N deles forma um único payload válido para
    envio em uma única conexão de socket."""
    return ''.join(montar_zpl_etiqueta(p) for p in paletes)


def enviar_zpl(zpl, host=None, port=None, timeout=5):
    host = host or settings.IMPRESSORA_ZPL_HOST
    port = port or settings.IMPRESSORA_ZPL_PORT
    if not host or not port:
        logger.error('Impressora ZPL não configurada (IMPRESSORA_ZPL_HOST/PORT); etiqueta não enviada.')
        return
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
