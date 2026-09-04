from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Sum
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

MODULOS = range(1, 21)
ANDARES = ['A', 'B', 'C']
ANDARES_DE_BAIXO_PARA_CIMA = ['C', 'B', 'A']
ITENS_POR_PAGINA_MAPA = 20
ITENS_POR_PAGINA_BUSCA = 20


def _nivel_ocupacao(ocupacao_atual, capacidade):
    if capacidade <= 0 or ocupacao_atual <= 0:
        return 'vazio'
    percentual = ocupacao_atual / capacidade * 100
    if percentual >= 100:
        return 'cheio'
    if percentual >= 70:
        return 'alto'
    if percentual >= 30:
        return 'medio'
    return 'baixo'


def _construir_grade(vagas_necessarias=None):
    """Monta a grade completa de endereços (todos, inclusive cheios/inativos),
    uma linha por andar e uma coluna por módulo/corredor — as linhas vêm na
    ordem C, B, A (`ANDARES_DE_BAIXO_PARA_CIMA`) pra grade ser lida de baixo
    pra cima, como os andares de um prédio real. Quando `vagas_necessarias`
    é informado (Put-away, Etapa B), cada célula ganha `disponivel=True` só
    se o endereço estiver ativo e tiver vagas suficientes pro lote inteiro —
    o template usa isso pra decidir se a célula vira um botão clicável.
    Quando `vagas_necessarias` é None (Mapa de Estoque), a grade é sempre
    somente leitura. É a mesma grade física usada tanto pelo Put-away
    ("Lado 01 — Entrada") quanto pelo Mapa de Estoque ("Lado 02 — Saída") —
    os dois nomes são só o rótulo de cada tela sobre os mesmos endereços."""
    enderecos_por_codigo = {e.codigo: e for e in Endereco.objects.all()}
    linhas = []
    for andar in ANDARES_DE_BAIXO_PARA_CIMA:
        celulas = []
        for modulo in MODULOS:
            codigo = Endereco.montar_codigo(lado=Endereco.LADO_ENTRADA, andar=andar, modulo=modulo)
            endereco = enderecos_por_codigo.get(codigo)
            if endereco:
                vagas = endereco.capacidade - endereco.ocupacao_atual
                celulas.append({
                    'endereco': endereco,
                    'nivel': _nivel_ocupacao(endereco.ocupacao_atual, endereco.capacidade),
                    'vagas': vagas,
                    'disponivel': (
                        vagas_necessarias is not None
                        and endereco.ativo
                        and vagas >= vagas_necessarias
                    ),
                })
            else:
                celulas.append(None)
        linhas.append({'andar': andar, 'celulas': celulas})
    return linhas


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
    """Maior número de vagas livres num único endereço ativo. Se o lote
    pendente for maior que isso, ele não cabe inteiro em lugar nenhum — é o
    gatilho da armazenagem parcial do Put-away (ver `alocar_lote`,
    `permitir_parcial`). São só 60 endereços, então a varredura em Python é
    irrelevante e evita expressão agregada sobre dois campos."""
    return max(
        (max(e.capacidade - e.ocupacao_atual, 0) for e in Endereco.objects.filter(ativo=True)),
        default=0,
    )


def _redirect_com_lote(lote):
    return redirect(f"{reverse('estoque:putaway')}?{urlencode({'lote': lote})}")


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
        if 'codigo_endereco' in request.POST:
            # Etapa B: aloca o lote no endereço escolhido na grade. Quando o
            # lote não cabe inteiro em nenhum endereço do armazém, a
            # alocação vira parcial — enche o endereço escolhido e devolve o
            # usuário para a mesma tela (?lote=) para escolher outro
            # endereço para o restante, até o lote inteiro estar armazenado.
            lote = (request.POST.get('lote') or '').strip()
            codigo_endereco = (request.POST.get('codigo_endereco') or '').strip()
            # permitir_parcial é uma permissão, não uma decisão: calculada
            # fora da transação, pode estar desatualizada, e nos dois
            # sentidos isso é inofensivo — se o lote passar a caber,
            # paletes[:vagas] devolve o lote inteiro (igual ao modo normal);
            # se deixar de caber, sai EnderecoCheioError e a próxima
            # renderização já mostra a grade em modo parcial.
            pendentes = Palete.objects.filter(estado='CONFERIDO_LOGISTICA', lote=lote).count()
            permitir_parcial = pendentes > _maior_vaga_disponivel()
            try:
                endereco, paletes = alocar_lote(
                    lote, codigo_endereco, usuario=request.user, permitir_parcial=permitir_parcial,
                )
            except LoteVazioError as exc:
                # Sem nada pendente não há Etapa B para voltar: volta pra Etapa A.
                messages.error(request, str(exc))
                return redirect('estoque:putaway')
            except Endereco.DoesNotExist:
                messages.error(request, f'Endereço "{codigo_endereco}" não encontrado, inativo ou não existe mais.')
                return _redirect_com_lote(lote)
            except EnderecoCheioError as exc:
                messages.error(request, f'{exc} Escolha outro endereço.')
                return _redirect_com_lote(lote)

            restantes = Palete.objects.filter(estado='CONFERIDO_LOGISTICA', lote=lote).count()
            if restantes:
                messages.success(
                    request,
                    f'{len(paletes)} palete(s) do lote {lote} armazenado(s) em {endereco.codigo}. '
                    f'Faltam {restantes} palete(s) — escolha o próximo endereço.',
                )
                return _redirect_com_lote(lote)

            messages.success(
                request,
                f'Lote {lote} ({len(paletes)} palete(s)) armazenado em {endereco.codigo}.',
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
    # Modo parcial: nenhum endereço comporta o lote inteiro. A grade passa a
    # aceitar qualquer endereço com ao menos 1 vaga (vagas_necessarias=1) —
    # _construir_grade não muda, só o argumento.
    modo_parcial = bool(paletes) and quantidade > maior_vaga
    sem_vagas = bool(paletes) and maior_vaga == 0
    return render(request, 'estoque/putaway.html', {
        'lote': lote,
        'paletes': paletes,
        'quantidade': quantidade,
        'peso_total': sum(p.peso_kg for p in paletes) if paletes else None,
        'grade': _construir_grade(vagas_necessarias=1 if modo_parcial else quantidade) if paletes else None,
        'grade_titulo': 'Lado 01 — Entrada',
        'modulos': MODULOS,
        'paletes_pendentes': (
            Palete.objects.filter(estado='CONFERIDO_LOGISTICA').order_by('lote', 'atualizado_em')
            if not lote else None
        ),
        'modo_parcial': modo_parcial,
        'sem_vagas': sem_vagas,
    })


def _marcar_caminho(grade, endereco_destino):
    """Marca, na grade já construída, a trilha de módulos até o endereço de
    destino: como o layout físico real do armazém (corredores, distâncias)
    não é modelado, assume-se um corredor único, em ordem crescente de módulo,
    do módulo 01 até o módulo de destino, no mesmo andar. Cada célula da
    trilha ganha `no_caminho=True`; a primeira célula que de fato existir na
    trilha ganha `eh_inicio=True` (protege contra o módulo 01 naquele andar
    não existir/estar inativo); a célula final também ganha
    `eh_destino=True` (se a trilha tiver um só módulo, a mesma célula
    acumula `eh_inicio` e `eh_destino`)."""
    primeira_celula_da_trilha = True
    for linha in grade:
        if linha['andar'] != endereco_destino.andar:
            continue
        for indice, celula in enumerate(linha['celulas']):
            modulo = MODULOS[indice]
            if modulo > endereco_destino.modulo:
                continue
            if celula:
                celula['no_caminho'] = True
                if primeira_celula_da_trilha:
                    celula['eh_inicio'] = True
                    primeira_celula_da_trilha = False
                if modulo == endereco_destino.modulo:
                    celula['eh_destino'] = True
    return grade


def _filtrar_grade(grade, andar=None, modulo=None):
    """Reduz a grade já construída ao filtro de 'Consultar por Endereço'
    (andar inteiro, corredor/módulo inteiro, ou ambos) — ao contrário de
    `_marcar_caminho` (que só destaca células numa grade completa), aqui as
    linhas/células fora do filtro são removidas de verdade, não só marcadas.
    Sem `andar` nem `modulo` (mapa inteiro), a grade completa volta
    inalterada."""
    linhas_filtradas = []
    for linha in grade:
        if andar is not None and linha['andar'] != andar:
            continue
        celulas = linha['celulas']
        if modulo is not None:
            celulas = [
                celula if (celula and celula['endereco'].modulo == modulo) else None
                for celula in celulas
            ]
        linhas_filtradas.append({'andar': linha['andar'], 'celulas': celulas})
    return linhas_filtradas


def _buscar_paletes_por_filtro_mapa(andar=None, modulo=None):
    """Lista os paletes ARMAZENADO do mapa inteiro, opcionalmente filtrados
    por andar e/ou módulo (modo 'Consultar por Endereço'). Sem nenhum
    filtro, devolve todos os ARMAZENADO — usado pela consulta de mapa
    inteiro."""
    paletes = Palete.objects.filter(estado='ARMAZENADO').select_related('endereco', 'area_destino')
    if andar:
        paletes = paletes.filter(endereco__andar=andar)
    if modulo is not None:
        paletes = paletes.filter(endereco__modulo=modulo)
    return paletes.order_by('endereco__modulo', 'endereco__andar')


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
    return {
        'endereco': endereco,
        'quantidade': 1,
        'peso_total': palete.peso_kg,
        'paletes': [palete],
        'grade': _marcar_caminho(_construir_grade(), endereco),
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
        'modulos': MODULOS,
    })


@group_required('Administradores', 'Gestor Produção', 'Gestor Logística')
def mapa_estoque(request):
    grade = _construir_grade()

    paginator_armazenados = Paginator(
        Palete.objects.filter(estado='ARMAZENADO')
        .select_related('endereco', 'area_destino')
        .order_by('endereco__modulo', 'endereco__andar', 'atualizado_em'),
        ITENS_POR_PAGINA_MAPA,
    )
    paletes_armazenados = paginator_armazenados.get_page(request.GET.get('pg_armazenados'))

    total_paletes_pendentes_armazenagem = Palete.objects.filter(estado='CONFERIDO_LOGISTICA').count()

    totais = Endereco.objects.filter(ativo=True).aggregate(
        capacidade=Sum('capacidade'), ocupacao=Sum('ocupacao_atual'),
    )
    capacidade_total = totais['capacidade'] or 0
    ocupacao_total = totais['ocupacao'] or 0
    percentual_geral = round(ocupacao_total / capacidade_total * 100) if capacidade_total else 0

    return render(request, 'estoque/mapa_estoque.html', {
        'grade': grade,
        'grade_titulo': 'Lado 02 — Saída',
        'modulos': MODULOS,
        'paletes_armazenados': paletes_armazenados,
        'total_paletes_armazenados': paginator_armazenados.count,
        'total_paletes_pendentes_armazenagem': total_paletes_pendentes_armazenagem,
        'capacidade_total': capacidade_total,
        'ocupacao_total': ocupacao_total,
        'percentual_geral': percentual_geral,
    })


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
    preenchida filtra, em AND. 'Data de fabricação' não é um campo próprio:
    é a data em que o palete foi criado no recebimento (`Palete.criado_em`),
    já que nada no recebimento é digitado livremente além de Produto/Lote
    (mesmo espírito de PESO_FIXO_RECEBIMENTO_KG). Em qualquer falha grava
    messages.error(...) e devolve None."""
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
        paletes = paletes.filter(criado_em__date=data_fabricacao)

    resultados = list(paletes.order_by('-criado_em'))
    if not resultados:
        messages.error(request, 'Nenhum palete encontrado para os critérios informados.')
        return None
    return resultados


@group_required('Administradores', 'Operador Produção', 'Operador Logística', 'Gestor Produção', 'Gestor Logística')
def consultar_palete(request):
    if request.method == 'POST':
        barcode = (request.POST.get('barcode_palete') or '').strip()

        if 'retirar' in request.POST:
            palete = _buscar_palete(request, barcode)
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
            'modo': 'palete',
            'barcode': barcode,
            'palete': palete,
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
            'modo': 'lote',
            'resultados': resultados,
            'lote': lote,
            'sku': sku,
            'data_fabricacao': data_fabricacao,
        })

    if modo == 'endereco':
        codigo_endereco = request.GET.get('codigo_endereco', '').strip()
        andar = request.GET.get('andar', '').strip()
        modulo_str = request.GET.get('modulo', '').strip()
        modulo = int(modulo_str) if modulo_str.isdigit() and int(modulo_str) in MODULOS else None

        filtro_submetido = not codigo_endereco and ('andar' in request.GET or 'modulo' in request.GET)
        resultados_mapa = None
        grade = _construir_grade()
        if filtro_submetido:
            grade = _filtrar_grade(grade, andar=andar or None, modulo=modulo)
            paginator = Paginator(_buscar_paletes_por_filtro_mapa(andar=andar or None, modulo=modulo), ITENS_POR_PAGINA_BUSCA)
            resultados_mapa = paginator.get_page(request.GET.get('pg'))

        endereco, paletes = _buscar_paletes_por_endereco(request, codigo_endereco)
        return render(request, 'estoque/consultar_palete.html', {
            'modo': 'endereco',
            'codigo_endereco': codigo_endereco,
            'endereco': endereco,
            'paletes': paletes,
            'grade': grade,
            'andares': ANDARES,
            'modulos': MODULOS,
            'andar': andar,
            'modulo': modulo_str,
            'filtro_submetido': filtro_submetido,
            'resultados_mapa': resultados_mapa,
        })

    return render(request, 'estoque/consultar_palete.html', {'modo': None})


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
