# CLAUDE.md — Projeto WMS (Café do Sítio)

> Este arquivo orienta o Claude Code neste repositório. A fonte única de verdade de requisitos é [docs/PRD.md](docs/PRD.md); veja também [docs/escopo.md](docs/escopo.md). Em caso de conflito, o PRD prevalece.

## Visão Geral

Sistema de controle de estoque por lote para a transição entre Produção e Logística da Café do Sítio. Camada de controle intermediária (entrada, armazenagem com mapa visual, separação por romaneio) — **não substitui o SAP B1**, que segue como fonte única de saldo.

## Stack

- **Backend:** Python / Django (`wms/` = projeto, `apps/` = aplicativos: `core`, `estoque`, `recebimento`)
- **Banco:** SQLite3 em desenvolvimento, MySQL em produção — troca ocorre apenas no bloco `DATABASES` de `wms/settings.py`
- **Frontend:** Views Django tradicionais, HTML5 + CSS3 puro (sem SPA). Layout otimizado para o pior caso: coletor Zebra MC2700 (4", 800×480)
- **Impressão:** Zebra ZT421 via ZPL bruto, socket TCP/IP porta `9100`
- **Coleta:** Zebra DataWedge em modo emulação de teclado (o scanner dispara `Enter` automático — os formulários devem oferecer bipagem contínua com `autofocus`)
- **Auth:** Django Auth nativo com `auth.Group` (`Administradores`, `Gestores`, `Operadores`) e `@permission_required`/mixins

## Regras de Negócio Invioláveis `[INVARIANTE]`

- O WMS nunca altera saldo do SAP B1 diretamente; sempre via requisições estruturadas (Outbox).
- Palete com SKU/Lote não validado, ou inconsistência grave, vira `DIVERGENTE` e trava o fluxo.
- O mapa de put-away só aparece para o operador após a transferência de saldo Produção→Logística estar processada no SAP B1.
- Endereço tem capacidade rígida de **21 paletes**. A validação de ocupação deve usar `.select_for_update()` para blindar contra condição de corrida — nunca confiar em check-then-save sem lock.
- Encerramento de guarda exige reautenticação por senha do Gestor.

## Fluxo Real do Palete (clarificado pelo usuário em 2026-07-17)

O PRD (seção 4.1) descreve a impressão como automática ("o Django valida... e despacha os bytes"). **Isso foi corrigido pelo usuário**: existe uma etapa de aprovação humana do Gestor entre o envio do colaborador e a impressão. A máquina de estados real do `Palete` é:

1. **Colaborador envia os dados** (SKU, Lote, Descrição, Peso) em `/recebimento/receber/`. O sistema só valida peso automaticamente: peso `<= 0` → `DIVERGENTE` (trava). Peso válido → fica `CRIADO` ("Aguardando Conferência do Gestor"). **Nenhuma impressão acontece aqui.**
2. **Gestor confere manualmente** em `/recebimento/conferencia/` (`apps/recebimento/views.py::conferencia_gestor`, protegida por `group_required('Gestores')`): vê os paletes `CRIADO` e decide:
   - **Aprova** + escolhe uma `AreaDestino` → estado vira `CONFERIDO`, `area_destino` é gravada, e só agora `disparar_impressao_async` é disparado (impressão real acontece na aprovação, não no envio).
   - **Rejeita** → estado vira `DIVERGENTE` (sem imprimir).
3. **Colaborador bipa o palete impresso** em `/estoque/putaway/` (`apps/estoque/views.py::putaway`) em **duas etapas**: (a) bipa só o `barcode_palete` → sistema mostra SKU/descrição/lote/peso; (b) só então bipa/digita o `codigo_endereco` disponível → `alocar_palete` grava `ARMAZENADO`.
4. **Gestor confirma o lote de guardas** com senha em `/relatorios/divergencias/` → `ARMAZENADO` vira `FINALIZADO` (sincronizado no SAP B1).

`AreaDestino` (`apps/estoque/models.py`) é um cadastro simples (nome + ativo, gerenciável via `/admin`) representando a área física de transição para onde o palete aprovado é fisicamente enviado antes do put-away. **Não vem populado por nenhum comando** — precisa ser cadastrado manualmente no admin antes de usar a tela de conferência do Gestor (não há nomes de área fixos definidos pelo negócio ainda).

## Estrutura de Endereçamento

Grade fixa: Corredores `01`–`20` × Andares `A`/`B`/`C` = 60 endereços, código no formato `CC-A` (ex.: `04-B`).

## Convenções do Projeto

- Apps vivem em `apps/<nome>` e são registrados em `INSTALLED_APPS` como `apps.<nome>`; cada `AppConfig.name` deve ser o caminho completo (`apps.core`, etc.) com `label` curto (`core`, etc.).
- Todo incremento deve ser testável via `python manage.py test` antes de avançar de milestone.
- Não pular etapas do roteiro de milestones (seção 5 do PRD) — cada um tem critério de aceite explícito.

## Roteiro de Milestones (status)

- [x] **M0 — Fundação:** projeto Django + apps (`core`, `estoque`, `recebimento`) registrados, `migrate`/`runserver` funcionando, `CLAUDE.md` criado.
- [x] **M1 — Autenticação e Grupos:** login/logout nativo (`django.contrib.auth.urls`), data migration `apps/core/migrations/0001_popular_grupos.py` populando `Administradores`/`Gestores`/`Operadores`, decorator `apps/core/decorators.py::group_required` protegendo a rota `relatorio_divergencias`. Testado em `apps/core/tests.py` (operador → 403, gestor → 200, anônimo → redirect login).
- [x] **M2 — Carga Inicial de Endereços:** modelo `Endereco` em `apps/estoque/models.py`; management command `apps/estoque/management/commands/gerar_estrutura_armazem.py` (idempotente, via `get_or_create`) criando as 60 posições (`CC-A`, `capacidade=21`, `ocupacao_atual=0`). Testado em `apps/estoque/tests.py` e verificado via banco real (60 registros únicos).
- [x] **M3 — Módulo de Entrada:** modelo `Palete` em `apps/recebimento/models.py` (barcode gerado no backend via `gerar_barcode_palete`, FK para `estoque.Endereco` e `estoque.AreaDestino`). View `receber_palete` só grava o envio do colaborador (`CRIADO`/`DIVERGENTE`, sem imprimir — ver "Fluxo Real do Palete" acima). View `conferencia_gestor` faz a aprovação/rejeição humana do Gestor e só ali dispara `apps/recebimento/zpl.py::disparar_impressao_async` (thread daemon, socket RAW TCP/IP porta 9100 configurável via `IMPRESSORA_ZPL_HOST`/`IMPRESSORA_ZPL_PORT`) sem bloquear a resposta HTTP. Testado em `apps/recebimento/tests.py` (montagem do ZPL, socket real via `socketserver` local, submissão/divergência/conferência com impressão mockada, permissões) e validado end-to-end via `runserver` nos três passos (envio → aprovação → impressão disparada).
- [x] **M4 — Put-away e Concorrência:** serviço `apps/estoque/services.py::alocar_palete` (`@transaction.atomic` + `.select_for_update()` no `Endereco`) movendo o palete para `ARMAZENADO` e incrementando `ocupacao_atual`, ou levantando `EnderecoCheioError` sem alterar nada. View `putaway` (`apps/estoque/views.py`) em duas etapas: bipa o palete → mostra o produto → bipa o endereço. Tela de confirmação do Gestor com reautenticação por senha estendendo `relatorio_divergencias` (`apps/core/views.py` — `ARMAZENADO` → `FINALIZADO` só após `request.user.check_password()`). Testado em `apps/estoque/tests.py` (rejeição determinística do 22º palete + teste de concorrência real com threads disputando a última vaga, 20/20 execuções estáveis) e `apps/core/tests.py` (senha certa finaliza, errada não altera). Validado end-to-end via `runserver` (recebimento → conferência do gestor → put-away → confirmação final).

  Nota: o invariante de que o mapa de put-away só aparece após a transferência SAP B1 Produção→Logística (seção 4.2.1 do PRD) **não foi implementado** — não há integração/Outbox com o SAP B1 modelada em nenhum milestone do roteiro; fica pendente para quando essa integração existir.

## Modelos Sugeridos (PRD seção 6)

`Endereco` — implementado em `apps/estoque/models.py` (Milestone 2).
`Palete` — implementado em `apps/recebimento/models.py` (Milestone 3).
