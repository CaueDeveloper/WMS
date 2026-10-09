from collections import Counter

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from urllib.parse import urlencode

from apps.core.decorators import group_required
from apps.recebimento.models import Palete

from .models import Endereco
from .services import (
    EnderecoCheioError,
    LoteVazioError,
    PaleteNaoArmazenadoError,
    alocar_lote,
    retirar_palete_do_mapa,
)

CORREDORES = Endereco.CORREDORES
ANDARES = list(Endereco.ANDARES)
POSICOES = Endereco.POSICOES
ITENS_POR_PAGINA_MAPA = 100
ITENS_POR_PAGINA_BUSCA = 20


def _andar_valido(valor, padrao=1):
    valor = str(valor or '').strip()
    return int(valor) if valor.isdigit() and int(valor) in ANDARES else padrao


def _corredor_valido(valor):
    valor = str(valor or '').strip().upper()
    return valor if len(valor) == 1 and valor in CORREDORES else None


def _construir_grade(andar, vagas_necessarias=None):
    """Monta a grade de um andar: uma linha por corredor (A–T) e uma coluna por
    posição (01–21). Cada célula é um endereço de 1 palete, com `estado`
    'livre', 'ocupado' ou 'inativo'. Cada linha traz `vagas` (posições livres
    do corredor naquele andar) e, quando `vagas_necessarias` é informado
    (Put-away), `disponivel=True` se o corredor comporta essa quantidade — o
    template usa isso pra habilitar o botão do corredor. Sem
    `vagas_necessarias` (Mapa de Estoque), a grade é somente leitura."""
    enderecos = {
        (e.corredor, e.posicao): e
        for e in Endereco.objects.filter(andar=andar).com_ocupacao()
    }
    linhas = []
    for corredor in CORREDORES:
        celulas = []
        vagas = 0
        for posicao in POSICOES:
            endereco = enderecos.get((corredor, posicao))
            if endereco is None:
                celulas.append(None)
                continue
            if endereco.ocupacao >= endereco.capacidade:
                estado = 'ocupado'
            elif endereco.ativo:
                estado = 'livre'
                vagas += 1
            else:
                estado = 'inativo'
            celulas.append({'endereco': endereco, 'estado': estado})
        linhas.append({
            'corredor': corredor,
            'celulas': celulas,
            'vagas': vagas,
            'disponivel': vagas_necessarias is not None and vagas >= vagas_necessarias,
        })
    return linhas


def _resumo_andares():
    """Posições livres/total por andar (endereços ativos), para o filtro de andar."""
    resumo = {a: {'numero': a, 'livres': 0, 'total': 0} for a in ANDARES}
    for endereco in Endereco.objects.filter(ativo=True).com_ocupacao():
        item = resumo[endereco.andar]
        item['total'] += endereco.capacidade
        item['livres'] += max(endereco.capacidade - endereco.ocupacao, 0)
    return list(resumo.values())


def _buscar_paletes_pendentes_do_lote(request, lote):
    """Busca todos os paletes CONFERIDO_LOGISTICA de `lote`, na mesma ordem
    (`atualizado_em, pk`) usada por `alocar_lote` para escolher quais alocar
    primeiro quando o lote não cabe inteiro num único endereço.
    Em qualquer falha grava messages.error(...) e devolve None."""
    lote = (lote or '').strip()
    if not lote:
        messages.error(request, 'Digite o número do lote.')
        return None
    paletes = list(
        Palete.objects.filter(estado='CONFERIDO_LOGISTICA', lote=lote).order_by('atualizado_em', 'pk')
    )
    if not paletes:
        messages.error(request, f'Lote "{lote}" não encontrado ou sem paletes pendentes de armazenagem.')
        return None
    return paletes


def _maior_vaga_disponivel():
    """Maior número de posições livres num único corredor/andar. Se o lote
    pendente for maior que isso, ele não cabe inteiro em lugar nenhum — é o
    gatilho da armazenagem parcial do Put-away (ver `alocar_lote`,
    `permitir_parcial`)."""
    # A ocupação precisa ser avaliada por posição ANTES de agrupar: filtrar
    # `ocupacao__lt` e agrupar na mesma query vira um HAVING por grupo.
    vagas = Counter()
    for e in Endereco.objects.filter(ativo=True).com_ocupacao():
        if e.ocupacao < e.capacidade:
            vagas[(e.corredor, e.andar)] += 1
    return max(vagas.values(), default=0)


def _redirect_com_lote(lote, andar=None):
    parametros = {'lote': lote}
    if andar:
        parametros['andar'] = andar
    return redirect(f"{reverse('estoque:putaway')}?{urlencode(parametros)}")


def _descrever_destino(enderecos):
    if len(enderecos) == 1:
        return enderecos[0].codigo
    return f'{enderecos[0].codigo} a {enderecos[-1].codigo}'


def _buscar_lote_por_barcode(request, barcode):
    """Busca o Palete pendente de armazenagem pelo barcode bipado e devolve
    seu `lote` (string) — a Etapa A só precisa disso pra montar a URL
    `?lote=` que alimenta a Etapa B (`_buscar_paletes_pendentes_do_lote`).
    Em qualquer falha grava messages.error(...) e devolve None."""
    barcode = (barcode or '').strip()
    if not barcode:
        messages.error(request, 'Bipe ou digite o código do palete.')
        return None
    palete = Palete.objects.filter(barcode_palete=barcode).first()
    if palete is None:
        messages.error(request, f'Palete "{barcode}" não encontrado. Confira o código e bipe novamente.')
        return None
    if palete.estado != 'CONFERIDO_LOGISTICA':
        messages.error(
            request,
            f'Palete {palete.barcode_palete} está "{palete.get_estado_display()}" — '
            'ainda não está pendente de armazenagem.',
        )
        return None
    return palete.lote


@group_required('Administradores', 'Operador Logística')
def putaway(request):
    if request.method == 'POST':
        if 'destino' in request.POST:
            # Etapa B: aloca o lote nas primeiras posições livres do corredor
            # escolhido (no andar filtrado). Quando o lote não cabe inteiro em
            # nenhum corredor/andar, a alocação vira parcial — enche o
            # corredor escolhido e devolve o usuário para a mesma tela
            # (?lote=) para escolher outro corredor para o restante, até o
            # lote inteiro estar armazenado.
            lote = (request.POST.get('lote') or '').strip()
            corredor = _corredor_valido(request.POST.get('destino'))
            andar = _andar_valido(request.POST.get('andar'))
            if corredor is None:
                messages.error(request, 'Corredor inválido. Escolha um corredor de A a T.')
                return _redirect_com_lote(lote, andar)
            # permitir_parcial é uma permissão, não uma decisão: calculada
            # fora da transação, pode estar desatualizada, e nos dois
            # sentidos isso é inofensivo — se o lote passar a caber,
            # paletes[:vagas] devolve o lote inteiro (igual ao modo normal);
            # se deixar de caber, sai EnderecoCheioError e a próxima
            # renderização já mostra a grade em modo parcial.
            pendentes = Palete.objects.filter(estado='CONFERIDO_LOGISTICA', lote=lote).count()
            permitir_parcial = pendentes > _maior_vaga_disponivel()
            try:
                enderecos, paletes = alocar_lote(
                    lote, corredor, andar, usuario=request.user, permitir_parcial=permitir_parcial,
                )
            except LoteVazioError as exc:
                # Sem nada pendente não há Etapa B para voltar: volta pra Etapa A.
                messages.error(request, str(exc))
                return redirect('estoque:putaway')
            except Endereco.DoesNotExist:
                messages.error(request, f'Corredor {corredor}, andar {andar} não encontrado ou inativo.')
                return _redirect_com_lote(lote, andar)
            except EnderecoCheioError as exc:
                messages.error(request, f'{exc} Escolha outro corredor.')
                return _redirect_com_lote(lote, andar)

            destino = _descrever_destino(enderecos)
            restantes = Palete.objects.filter(estado='CONFERIDO_LOGISTICA', lote=lote).count()
            if restantes:
                messages.success(
                    request,
                    f'{len(paletes)} palete(s) do lote {lote} armazenado(s) em {destino}. '
                    f'Faltam {restantes} palete(s) — escolha o próximo corredor.',
                )
                return _redirect_com_lote(lote, andar)

            messages.success(
                request,
                f'Lote {lote} ({len(paletes)} palete(s)) armazenado em {destino}.',
            )
            return redirect('estoque:putaway')

        # Etapa A: identifica o lote a partir do palete bipado.
        barcode_palete = (request.POST.get('barcode_palete') or '').strip()
        lote = _buscar_lote_por_barcode(request, barcode_palete)
        if lote is None:
            return redirect('estoque:putaway')
        return _redirect_com_lote(lote)

    # GET: ?lote=<lote> mostra a Etapa B (resumo do lote + grade de endereços);
    # sem parâmetro mostra a Etapa A (lista de pendentes + campo de bipar palete).
    lote = request.GET.get('lote', '').strip()
    paletes = None
    if lote:
        paletes = _buscar_paletes_pendentes_do_lote(request, lote)
        if paletes is None:
            return redirect('estoque:putaway')

    quantidade = len(paletes) if paletes else 0
    maior_vaga = _maior_vaga_disponivel() if paletes else 0
    # Modo parcial: nenhum corredor/andar comporta o lote inteiro. A grade
    # passa a aceitar qualquer corredor com ao menos 1 posição livre
    # (vagas_necessarias=1) — _construir_grade não muda, só o argumento.
    modo_parcial = bool(paletes) and quantidade > maior_vaga
    sem_vagas = bool(paletes) and maior_vaga == 0
    andar = _andar_valido(request.GET.get('andar'))
    return render(request, 'estoque/putaway.html', {
        'lote': lote,
        'paletes': paletes,
        'quantidade': quantidade,
        'peso_total': sum(p.peso_kg for p in paletes) if paletes else None,
        'grade': _construir_grade(andar, vagas_necessarias=1 if modo_parcial else quantidade) if paletes else None,
        'grade_titulo': f'Andar {andar}',
        'posicoes': POSICOES,
        'andar': andar,
        'andares': _resumo_andares() if paletes else None,
        'query_extra': urlencode({'lote': lote}) + '&' if lote else '',
        'paletes_pendentes': (
            Palete.objects.filter(estado='CONFERIDO_LOGISTICA').order_by('lote', 'atualizado_em')
            if not lote else None
        ),
        'modo_parcial': modo_parcial,
        'sem_vagas': sem_vagas,
    })


def _marcar_caminho(grade, endereco_destino):
    """Marca, na grade do andar já construída, a trilha até o endereço de
    destino: percorre o corredor do destino em ordem crescente de posição, da
    posição 01 até a posição do palete. Cada célula da trilha ganha
    `no_caminho=True`; a primeira célula que de fato existir na trilha ganha
    `eh_inicio=True`; a célula final também ganha `eh_destino=True` (se a
    trilha tiver uma só posição, a mesma célula acumula `eh_inicio` e
    `eh_destino`)."""
    primeira_celula_da_trilha = True
    for linha in grade:
        if linha['corredor'] != endereco_destino.corredor:
            continue
        for posicao, celula in zip(POSICOES, linha['celulas']):
            if posicao > endereco_destino.posicao or not celula:
                continue
            celula['no_caminho'] = True
            if primeira_celula_da_trilha:
                celula['eh_inicio'] = True
                primeira_celula_da_trilha = False
            if posicao == endereco_destino.posicao:
                celula['eh_destino'] = True
    return grade


def _filtrar_grade(grade, corredor=None):
    """Reduz a grade do andar ao corredor escolhido em 'Consultar por
    Endereço' — as linhas fora do filtro são removidas de verdade, não só
    marcadas. Sem `corredor`, a grade volta inalterada."""
    if corredor is None:
        return grade
    return [linha for linha in grade if linha['corredor'] == corredor]


def _buscar_armazenamento_do_palete(request, barcode):
    """Busca o Palete ARMAZENADO de `barcode` e monta seu grupo (endereço,
    trajeto). Em qualquer falha grava messages.error(...) e devolve None."""
    barcode = (barcode or '').strip()
    if not barcode:
        messages.error(request, 'Bipe ou digite o código do palete.')
        return None
    palete = Palete.objects.filter(barcode_palete=barcode).select_related('endereco').first()
    if palete is None:
        messages.error(request, f'Palete "{barcode}" não encontrado. Confira o código e bipe novamente.')
        return None
    if palete.estado != 'ARMAZENADO':
        messages.error(
            request,
            f'Palete {palete.barcode_palete} está "{palete.get_estado_display()}" — '
            'ainda não foi armazenado.',
        )
        return None

    endereco = palete.endereco
    if endereco is None:
        messages.error(request, f'Palete {palete.barcode_palete} está armazenado, mas sem endereço registrado.')
        return None
    return {
        'endereco': endereco,
        'quantidade': 1,
        'peso_total': palete.peso_kg,
        'paletes': [palete],
        'grade': _marcar_caminho(_construir_grade(endereco.andar), endereco),
    }


@group_required('Administradores', 'Operador Logística')
def plano_armazenagem(request):
    barcode = request.GET.get('barcode_palete', '').strip()
    grupo = None
    if barcode:
        grupo = _buscar_armazenamento_do_palete(request, barcode)
    return render(request, 'estoque/plano_armazenagem.html', {
        'barcode_palete': barcode,
        'grupo': grupo,
        'posicoes': POSICOES,
    })


VISOES_MAPA = (
    ('ocupacao', 'Ocupação geral', 'chart'),
    ('andares', 'Andares', 'layers'),
    ('corredores', 'Corredores', 'columns'),
    ('guardas', 'Guardas', 'cube'),
    ('geral', 'Visão geral', 'map'),
)
NIVEIS_MAPA = (('livre', 'Livre'), ('ocupado', 'Ocupado'), ('inativo', 'Inativo'))
MODOS_MAPA = (('simples', 'Simples'), ('complexo', 'Complexo'))


def _estado_endereco(endereco):
    if endereco.ocupacao >= endereco.capacidade:
        return 'ocupado'
    return 'livre' if endereco.ativo else 'inativo'


_ROTULOS_FILTRO = {
    'andar': lambda v: f'Andar {v}',
    'nivel': lambda v: dict(NIVEIS_MAPA)[v],
    'corredor': lambda v: f'Corredor {v}',
    'guarda': lambda v: f'Guarda {v:02d}',
}


def _filtros_mapa(get):
    """Lê os filtros do Mapa de Estoque (andar, nível de ocupação, corredor,
    guarda) da querystring; valor inválido ou ausente vira None (sem filtro)."""
    andar = str(get.get('andar') or '').strip()
    guarda = str(get.get('guarda') or '').strip()
    nivel = str(get.get('nivel') or '').strip()
    return {
        'andar': int(andar) if andar.isdigit() and int(andar) in ANDARES else None,
        'nivel': nivel if nivel in dict(NIVEIS_MAPA) else None,
        'corredor': _corredor_valido(get.get('corredor')),
        'guarda': int(guarda) if guarda.isdigit() and int(guarda) in POSICOES else None,
    }


def _enderecos_mapa(filtros):
    """Endereços que passam pelos filtros, ordenados por andar/corredor/guarda,
    cada um com `estado` ('livre', 'ocupado' ou 'inativo')."""
    consulta = Endereco.objects.com_ocupacao().order_by('andar', 'corredor', 'posicao')
    if filtros['andar']:
        consulta = consulta.filter(andar=filtros['andar'])
    if filtros['corredor']:
        consulta = consulta.filter(corredor=filtros['corredor'])
    if filtros['guarda']:
        consulta = consulta.filter(posicao=filtros['guarda'])
    enderecos = list(consulta)
    for endereco in enderecos:
        endereco.estado = _estado_endereco(endereco)
    if filtros['nivel']:
        enderecos = [e for e in enderecos if e.estado == filtros['nivel']]
    return enderecos


def _resumir_enderecos(enderecos):
    """Totais por estado e % de ocupação (ocupados sobre ativos). `nivel` é a
    faixa de cor (baixo/medio/alto/cheio/vazio) usada no modo simples."""
    livres = sum(1 for e in enderecos if e.estado == 'livre')
    ocupados = sum(1 for e in enderecos if e.estado == 'ocupado')
    ativos = livres + ocupados
    percentual = round(ocupados / ativos * 100) if ativos else 0
    if not ativos:
        nivel = 'vazio'
    elif percentual >= 100:
        nivel = 'cheio'
    elif percentual >= 75:
        nivel = 'alto'
    elif percentual >= 50:
        nivel = 'medio'
    else:
        nivel = 'baixo'
    return {
        'total': len(enderecos),
        'livres': livres,
        'ocupados': ocupados,
        'inativos': len(enderecos) - ativos,
        'percentual': percentual,
        'nivel': nivel,
        'paletes': sum(e.ocupacao for e in enderecos),
    }


def _agrupar(enderecos, chave):
    grupos = {}
    for endereco in enderecos:
        grupos.setdefault(getattr(endereco, chave), []).append(endereco)
    return grupos


def _grade_do_andar(enderecos):
    """Grade (corredor × guarda) só com os corredores e guardas presentes em
    `enderecos` (os já filtrados de um andar). Células sem endereço são None."""
    por_posicao = {(e.corredor, e.posicao): e for e in enderecos}
    corredores = [c for c in CORREDORES if any(e.corredor == c for e in enderecos)]
    posicoes = sorted({e.posicao for e in enderecos})
    linhas = [
        {'corredor': corredor, 'celulas': [por_posicao.get((corredor, posicao)) for posicao in posicoes]}
        for corredor in corredores
    ]
    return {'posicoes': posicoes, 'linhas': linhas}


def _url_mapa(parametros, **alterar):
    """URL do mapa com os parâmetros atuais, sobrescrevendo/removendo (valor
    None) os de `alterar`. Mudar qualquer coisa que não seja a página volta
    para a página 1."""
    novos = {**parametros, **alterar}
    if 'pg' not in alterar:
        novos.pop('pg', None)
    novos = {k: v for k, v in novos.items() if v not in (None, '')}
    base = reverse('estoque:mapa_estoque')
    return f'{base}?{urlencode(novos)}' if novos else base


def _opcoes(parametros, chave, valores, atual):
    opcoes = [{'valor': '', 'rotulo': 'Todos', 'url': _url_mapa(parametros, **{chave: None}), 'ativo': atual is None}]
    for valor, rotulo in valores:
        opcoes.append({
            'valor': valor,
            'rotulo': rotulo,
            'url': _url_mapa(parametros, **{chave: valor}),
            'ativo': atual is not None and str(atual) == str(valor),
        })
    return opcoes


@group_required('Administradores', 'Gestor Produção', 'Gestor Logística')
def mapa_estoque(request):
    visao = request.GET.get('visao')
    visao = visao if visao in {v[0] for v in VISOES_MAPA} else 'ocupacao'
    modo = request.GET.get('modo')
    modo = modo if modo in dict(MODOS_MAPA) else 'simples'
    filtros = _filtros_mapa(request.GET)

    parametros = {'visao': visao, 'modo': modo}
    parametros.update({k: v for k, v in filtros.items() if v is not None})

    enderecos = _enderecos_mapa(filtros)
    corredores_existentes = Endereco.objects.values_list('corredor', flat=True).distinct()
    contexto = {
        'visao': visao,
        'modo': modo,
        'filtros': filtros,
        'tem_filtro': any(v is not None for v in filtros.values()),
        'resumo': _resumir_enderecos(enderecos),
        'visoes': [
            {'chave': chave, 'rotulo': rotulo, 'icone': icone,
             'url': _url_mapa(parametros, visao=chave), 'ativo': chave == visao}
            for chave, rotulo, icone in VISOES_MAPA
        ],
        'modos': [
            {'chave': chave, 'rotulo': rotulo, 'url': _url_mapa(parametros, modo=chave), 'ativo': chave == modo}
            for chave, rotulo in MODOS_MAPA
        ],
        'url_limpar': _url_mapa({'visao': visao, 'modo': modo}),
        'filtros_ativos': [
            {'rotulo': rotulo(filtros[chave]), 'url': _url_mapa(parametros, **{chave: None})}
            for chave, rotulo in _ROTULOS_FILTRO.items()
            if filtros[chave] is not None
        ],
        'opcoes_andar': _opcoes(parametros, 'andar', [(a, f'Andar {a}') for a in ANDARES], filtros['andar']),
        'opcoes_nivel': _opcoes(parametros, 'nivel', NIVEIS_MAPA, filtros['nivel']),
        'opcoes_corredor': _opcoes(
            parametros, 'corredor', [(c, c) for c in CORREDORES if c in set(corredores_existentes)], filtros['corredor'],
        ),
        'opcoes_guarda': _opcoes(parametros, 'guarda', [(p, f'{p:02d}') for p in POSICOES], filtros['guarda']),
    }

    if visao == 'ocupacao':
        contexto['por_andar'] = [
            {
                'andar': andar,
                'resumo': _resumir_enderecos(itens),
                'url': _url_mapa(parametros, visao='andares', andar=andar),
            }
            for andar, itens in sorted(_agrupar(enderecos, 'andar').items())
        ]
    elif visao in ('andares', 'geral'):
        contexto['andares_grade'] = [
            {'andar': andar, 'resumo': _resumir_enderecos(itens), 'grade': _grade_do_andar(itens)}
            for andar, itens in sorted(_agrupar(enderecos, 'andar').items())
        ]
    elif visao == 'corredores':
        contexto['corredores'] = [
            {
                'corredor': corredor,
                'resumo': _resumir_enderecos(itens),
                'url': _url_mapa(parametros, visao='guardas', corredor=corredor),
                'por_andar': [
                    {'andar': andar, 'resumo': _resumir_enderecos(sub)}
                    for andar, sub in sorted(_agrupar(itens, 'andar').items())
                ],
            }
            for corredor, itens in sorted(_agrupar(enderecos, 'corredor').items())
        ]
    else:
        pagina = Paginator(enderecos, ITENS_POR_PAGINA_MAPA).get_page(request.GET.get('pg'))
        barcodes = dict(
            Palete.objects.filter(estado='ARMAZENADO', endereco__in=[e.pk for e in pagina])
            .values_list('endereco_id', 'barcode_palete')
        )
        for endereco in pagina:
            endereco.barcode = barcodes.get(endereco.pk)
        contexto['pagina'] = pagina
        contexto['url_anterior'] = (
            _url_mapa(parametros, pg=pagina.previous_page_number()) if pagina.has_previous() else None
        )
        contexto['url_proximo'] = _url_mapa(parametros, pg=pagina.next_page_number()) if pagina.has_next() else None

    return render(request, 'estoque/mapa_estoque.html', contexto)


def _buscar_palete(request, barcode):
    """Busca Palete pelo barcode_palete, sem restrição de estado.
    Em caso de não encontrado grava messages.error(...) e devolve None."""
    barcode = (barcode or '').strip()
    if not barcode:
        messages.error(request, 'Bipe ou digite o código do palete.')
        return None
    palete = Palete.objects.select_related('endereco', 'area_destino').filter(barcode_palete=barcode).first()
    if palete is None:
        messages.error(request, f'Palete "{barcode}" não encontrado. Confira o código e bipe novamente.')
        return None
    return palete


def _exigir_armazenado(request, palete):
    if palete.estado != 'ARMAZENADO':
        messages.error(
            request,
            f'Palete {palete.barcode_palete} está "{palete.get_estado_display()}" '
            '— não é possível retirar agora.',
        )
        return False
    return True


def _redirect_com_barcode(barcode):
    return redirect(f"{reverse('estoque:consultar_palete')}?{urlencode({'modo': 'palete', 'barcode': barcode})}")


def _buscar_paletes_por_endereco(request, codigo_endereco):
    """Busca os paletes ARMAZENADO no endereço de código `codigo_endereco`
    (modo "Consultar por Endereço"). Em qualquer falha grava
    messages.error(...) e devolve None."""
    codigo_endereco = (codigo_endereco or '').strip()
    if not codigo_endereco:
        return None, None
    endereco = Endereco.objects.filter(codigo=codigo_endereco).first()
    if endereco is None:
        messages.error(request, f'Endereço "{codigo_endereco}" não encontrado.')
        return None, None
    paletes = list(
        Palete.objects.filter(estado='ARMAZENADO', endereco=endereco)
        .select_related('endereco', 'area_destino').order_by('atualizado_em', 'pk')
    )
    return endereco, paletes


def _buscar_paletes_por_criterios(request, lote, sku, data_fabricacao_str):
    """Busca Palete por lote/SKU/data de fabricação — qualquer combinação
    preenchida filtra, em AND. A data filtra `Palete.data_fabricacao`; paletes
    antigos (sem o campo) são casados pela data de criação (`criado_em`). Em
    qualquer falha grava messages.error(...) e devolve None."""
    lote = (lote or '').strip().upper()
    sku = (sku or '').strip()
    data_fabricacao_str = (data_fabricacao_str or '').strip()

    if not lote and not sku and not data_fabricacao_str:
        messages.error(request, 'Informe ao menos um critério de busca (lote, SKU ou data de fabricação).')
        return None

    paletes = Palete.objects.select_related('endereco', 'area_destino')
    if lote:
        paletes = paletes.filter(lote=lote)
    if sku:
        paletes = paletes.filter(sku=sku)
    if data_fabricacao_str:
        data_fabricacao = parse_date(data_fabricacao_str)
        if data_fabricacao is None:
            messages.error(request, 'Data de fabricação inválida.')
            return None
        paletes = paletes.filter(
            Q(data_fabricacao=data_fabricacao)
            | Q(data_fabricacao__isnull=True, criado_em__date=data_fabricacao)
        )

    resultados = list(paletes.order_by('-criado_em'))
    if not resultados:
        messages.error(request, 'Nenhum palete encontrado para os critérios informados.')
        return None
    return resultados


def _pode_retirar(user):
    return user.is_superuser or user.groups.filter(name__in=('Administradores', 'Operador Logística')).exists()


@group_required('Administradores', 'Operador Produção', 'Operador Logística', 'Gestor Produção', 'Gestor Logística')
def consultar_palete(request):
    def _linha_do_tempo(palete):
        """Uma linha por etapa (criação, aprovações, armazenagem, retirada)
        com quem fez e quando; etapa ainda não ocorrida fica com evento None."""
        etapas = [
            ('CRIADO', 'Fabricado / criado por'),
            ('REPROVADO', 'Reprovado por'),
            ('CONFERIDO', 'Aprovado na Produção por'),
            ('CONFERIDO_LOGISTICA', 'Aprovado na Logística por'),
            ('ARMAZENADO', 'Armazenado por'),
            ('RETIRADO', 'Retirado por'),
        ]
        eventos = {}
        for h in palete.historico.select_related('usuario'):
            eventos[h.estado] = h  # a ocorrência mais recente de cada etapa prevalece
        return [{'rotulo': rotulo, 'evento': eventos.get(estado)} for estado, rotulo in etapas]

    if request.method == 'POST':
        barcode = (request.POST.get('barcode_palete') or '').strip()

        if 'retirar' in request.POST:
            palete = _buscar_palete(request, barcode)
            if not _pode_retirar(request.user):
                messages.error(request, 'Apenas a Logística pode retirar paletes do mapa.')
                palete = None
            if palete is not None and _exigir_armazenado(request, palete):
                try:
                    retirar_palete_do_mapa(palete, usuario=request.user)
                except PaleteNaoArmazenadoError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, f'Palete {palete.barcode_palete} retirado do mapa — saída física registrada.')

            codigo_endereco = (request.POST.get('codigo_endereco') or '').strip()
            if codigo_endereco:
                return redirect(
                    f"{reverse('estoque:consultar_palete')}?"
                    f"{urlencode({'modo': 'endereco', 'codigo_endereco': codigo_endereco})}"
                )
            return _redirect_com_barcode(barcode) if barcode else redirect('estoque:consultar_palete')

        return redirect('estoque:consultar_palete')

    modo = request.GET.get('modo', '').strip()

    if modo == 'palete':
        barcode = request.GET.get('barcode', '').strip()
        palete = None
        if barcode:
            palete = _buscar_palete(request, barcode)
        return render(request, 'estoque/consultar_palete.html', {
            'pode_retirar': _pode_retirar(request.user),
            'modo': 'palete',
            'barcode': barcode,
            'palete': palete,
            'linha_do_tempo': _linha_do_tempo(palete) if palete else None,
        })

    if modo == 'lote':
        lote = request.GET.get('lote', '').strip()
        sku = request.GET.get('sku', '').strip()
        data_fabricacao = request.GET.get('data_fabricacao', '').strip()
        resultados = None
        busca_submetida = any(campo in request.GET for campo in ('lote', 'sku', 'data_fabricacao'))
        if busca_submetida:
            paletes = _buscar_paletes_por_criterios(request, lote, sku, data_fabricacao)
            if paletes is not None:
                paginator = Paginator(paletes, ITENS_POR_PAGINA_BUSCA)
                resultados = paginator.get_page(request.GET.get('pg'))
        return render(request, 'estoque/consultar_palete.html', {
            'pode_retirar': _pode_retirar(request.user),
            'modo': 'lote',
            'resultados': resultados,
            'lote': lote,
            'sku': sku,
            'data_fabricacao': data_fabricacao,
        })

    if modo == 'endereco':
        codigo_endereco = request.GET.get('codigo_endereco', '').strip()
        andar_str = request.GET.get('andar', '').strip()
        andar_filtro = _andar_valido(andar_str, padrao=None)
        corredor = _corredor_valido(request.GET.get('corredor'))

        filtro_submetido = not codigo_endereco and ('andar' in request.GET or 'corredor' in request.GET)
        grade = _construir_grade(andar_filtro or 1)
        if filtro_submetido:
            grade = _filtrar_grade(grade, corredor=corredor)

        endereco, paletes = _buscar_paletes_por_endereco(request, codigo_endereco)
        return render(request, 'estoque/consultar_palete.html', {
            'pode_retirar': _pode_retirar(request.user),
            'modo': 'endereco',
            'codigo_endereco': codigo_endereco,
            'endereco': endereco,
            'paletes': paletes,
            'grade': grade,
            'andares': ANDARES,
            'corredores': CORREDORES,
            'posicoes': POSICOES,
            'andar': str(andar_filtro or ''),
            'corredor': corredor or '',
            'grade_titulo': f'Andar {andar_filtro or 1}',
            'filtro_submetido': filtro_submetido,
        })

    return render(request, 'estoque/consultar_palete.html', {
            'pode_retirar': _pode_retirar(request.user),'modo': None})


@group_required('Administradores', 'Gestor Produção', 'Gestor Logística')
def inspecionar_palete(request, pk):
    palete = get_object_or_404(
        Palete.objects.select_related('endereco', 'area_destino'), pk=pk,
    )
    historico = palete.historico.select_related('usuario').order_by('criado_em')
    return render(request, 'estoque/inspecionar_palete.html', {
        'palete': palete,
        'historico': historico,
    })
