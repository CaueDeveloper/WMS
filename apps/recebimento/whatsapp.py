import json
import logging
import threading
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)


def montar_mensagem_lotes(aprovados):
    """Monta o texto de uma única notificação WhatsApp cobrindo todos os lotes
    aprovados numa mesma ação do Gestor. `aprovados`: list[(lote, paletes)], no
    formato retornado por services.aprovar_lotes_pendentes. Não menciona área de
    destino — essa atribuição foi adiada para uma etapa futura fora deste fluxo."""
    aprovados = list(aprovados)
    total_paletes = sum(len(paletes) for _, paletes in aprovados)
    total_peso = sum(p.peso_kg for _, paletes in aprovados for p in paletes)

    secoes = []
    for lote, paletes in aprovados:
        peso_lote = sum(p.peso_kg for p in paletes)
        sku = paletes[0].sku if paletes else '—'
        descricao = paletes[0].descricao if paletes else '—'
        barcodes = '\n'.join(f'- {p.barcode_palete}' for p in paletes)
        secoes.append(
            f'Lote: {lote}\n'
            f'SKU: {sku} — {descricao}\n'
            f'Quantidade de paletes: {len(paletes)}\n'
            f'Peso: {peso_lote} kg\n'
            f'Paletes:\n{barcodes}'
        )

    cabecalho = (
        'Lotes aprovados para transferência\n'
        f'Total de lotes: {len(aprovados)}\n'
        f'Total de paletes: {total_paletes}\n'
        f'Peso total: {total_peso} kg'
    )
    return cabecalho + '\n\n' + '\n\n'.join(secoes)


def enviar_whatsapp(mensagem, numero=None, url=None, timeout=5):
    numero = numero or settings.ZAPI_LOGISTICA_WHATSAPP_NUMBER
    url = url or (
        f'https://api.z-api.io/instances/{settings.ZAPI_INSTANCE_ID}'
        f'/token/{settings.ZAPI_TOKEN}/send-text'
    )
    payload = json.dumps({'phone': numero, 'message': mensagem}).encode('utf-8')
    req = urllib.request.Request(
        url, data=payload, method='POST',
        headers={'Content-Type': 'application/json', 'Client-Token': settings.ZAPI_CLIENT_TOKEN},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except (urllib.error.URLError, OSError):
        logger.exception('Falha ao enviar notificação WhatsApp via Z-API para %s', numero)


def disparar_notificacao_async(aprovados):
    """Envia a notificação combinada (todos os lotes aprovados numa mesma ação) em
    uma thread separada para não travar a requisição HTTP."""
    mensagem = montar_mensagem_lotes(aprovados)
    thread = threading.Thread(target=enviar_whatsapp, args=(mensagem,), daemon=True)
    thread.start()
    return thread
