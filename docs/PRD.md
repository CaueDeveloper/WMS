# PRD — Projeto WMS (Controle de Estoque por Lote)

> Café do Sítio · Produção → Logística
> Stack: Python/Django + SQLite (dev) / MySQL (prod) + HTML5/CSS3 (sem SPA)
> Status: refletindo o código atual (atualizado em 2026-10). Itens marcados **[DESABILITADO]** ou **[FORA DO ESCOPO ATUAL]** existiram no desenho original e hoje não fazem parte do sistema.

Marcadores: `[ASSUMPTION]` = premissa técnica adotada; `[INVARIANTE]` = regra de negócio que nenhuma camada pode violar.

---

## 1. Contexto

Controle de estoque por lote na transição entre Produção e Logística: emissão de etiqueta, transferência entre setores com dupla conferência (Produção e Logística), armazenagem com mapa visual e consulta/rastreabilidade por palete e lote. O **SAP B1 continua sendo a única fonte de saldo**; o WMS controla o fluxo físico.

Problema original: pesagem e conferência manuais, informadas por WhatsApp, sem rastreabilidade confiável.

---

## 2. Decisões de Arquitetura

| # | Tema | Decisão |
|---|------|---------|
| D1 | Fonte de saldo | SAP B1. A integração de escrita (Outbox) **[FORA DO ESCOPO ATUAL]**. |
| D2 | Persistência | Django ORM. SQLite em dev, MySQL em produção (bloco `DATABASES`, variáveis `DJANGO_DB_*`). |
| D3 | Interface | Views Django + HTML5/CSS (Tailwind compilado). Sem SPA. |
| D4 | Coleta | Coletores Zebra MC2700/TC52 via DataWedge (teclado). Pior caso de tela: 800×480. |
| D5 | Impressão | Zebra ZT421, ZPL bruto via socket TCP na porta 9100 (`IMPRESSORA_ZPL_HOST/PORT`). Envio em thread, disparado após o commit. |
| D6 | Endereçamento | Corredor **A–T** × Andar **1–3** × Posição **1–21** = **1260** endereços. Código `001-02-A-01-01` (`001-02` fixo). **Capacidade 1 palete por posição.** Não existe contador de ocupação: ela é contada pelos paletes `ARMAZENADO`. |
| D7 | Autenticação | LDAP/Active Directory (`django-auth-ldap`) sobre Django Auth. Permissão por `auth.Group` via `@group_required`. |
| D8 | Notificação | Z-API/WhatsApp **removido**; aviso à Logística é manual. |
| D9 | HTTPS | Servidor `cheroot` com TLS direto (`deploy/serve_https.py`), sem proxy reverso. |

`[ASSUMPTION]` O coletor opera online (Wi-Fi) e o scanner envia `Enter` ao final da leitura (bipagem contínua).
`[ASSUMPTION]` O certificado LDAPS não é validado hoje (`OPT_X_TLS_NEVER`) — decisão mantida até a CA interna estar disponível.

---

## 3. Perfis (Django Groups)

| Grupo | Acesso |
|-------|--------|
| **Administradores** | Tudo, incluindo a tela Usuários (atribuição de perfis a partir do AD). |
| **Operador Produção** | Emitir etiquetas; bipar paletes na transferência da Produção; reprovar. |
| **Gestor Produção** | Aprovar lote na Produção; corrigir paletes reprovados; reprovar; relatório de aprovação; estatísticas. |
| **Operador Logística** | Bipar na conferência da Logística; reprovar; Put-away; retirar palete do mapa. |
| **Gestor Logística** | Aprovar lote na Logística; reprovar; relatório de aprovação; estatísticas. |
| Todos os operacionais | Mapa de estoque e consultas (palete, lote). |

Usuário novo de Produção/Logística nasce **inativo** e precisa ser ativado por um Administrador. Os grupos vêm do AD apenas no primeiro login; depois são geridos na tela Usuários. Administradores não ganham `is_staff` automaticamente (o `/admin` exige superusuário).

---

## 4. Fluxo e Regras de Negócio

### 4.1 Máquina de estados do palete

```
FABRICADO ──bip Produção──▶ TRANSFERENCIA_PRODUCAO ──aprova lote (Gestor Prod.)──▶ APROVADO_PRODUCAO
APROVADO_PRODUCAO ──bip Logística──▶ TRANSFERENCIA_LOGISTICA ──aprova lote (Gestor Log.)──▶ APROVADO_LOGISTICA
APROVADO_LOGISTICA ──Put-away──▶ ARMAZENADO ──retirar──▶ RETIRADO (terminal)
(FABRICADO | TRANSFERENCIA_PRODUCAO | APROVADO_PRODUCAO | TRANSFERENCIA_LOGISTICA) ──reprovar──▶ REPROVADO ──corrigir (Gestor Prod.)──▶ FABRICADO
```

O status `FINALIZADO` (SAP) foi removido do modelo (migração 0017); paletes antigos nele voltam a `ARMAZENADO`. Toda transição grava uma linha em `PaleteHistorico` (usuário e data/hora).

### 4.2 Recebimento / emissão (Produção)
1. Operador escolhe o produto, informa lote, quantidade (1–50) e data de fabricação (não futura).
2. Peso padrão: 500 kg. O checkbox **"Peso diferente de 500 kg"** libera a digitação do peso; produtos cadastrados com `peso_variavel` já o marcam automaticamente.
3. `[INVARIANTE]` O **lote** aceita só `A-Z 0-9 . _ / -` (é gravado em ZPL e em URLs) e não pode receber novos paletes depois de aprovado.
4. Os paletes são gravados com o histórico `FABRICADO` na mesma transação; as etiquetas são impressas após o commit, sem aguardar aprovação.

### 4.3 Transferência
- **Produção:** o Operador bipa cada palete (`FABRICADO → TRANSFERENCIA_PRODUCAO`). O Gestor só aprova o lote quando **todos** os paletes foram bipados e não há reprovados pendentes.
- **Logística:** o Operador bipa (`APROVADO_PRODUCAO → TRANSFERENCIA_LOGISTICA`); o Gestor aprova o lote inteiro. `[INVARIANTE]` O lote não é aprovado na Logística se ainda houver paletes dele na Produção (`FABRICADO`/`TRANSFERENCIA_PRODUCAO`) ou reprovados.
- **Reprovação:** qualquer operador/gestor da etapa reprova com motivo opcional. O Gestor Produção corrige item, peso e data; o palete volta a `FABRICADO`. **Se peso ou data de fabricação mudarem, uma nova etiqueta é reimpressa automaticamente** (a anterior deve ser substituída).
- Bipagens e aprovações são atômicas (linha travada com `select_for_update`).
- Após aprovar, o Gestor pode emitir o **relatório de aprovação** (Produção ou Logística) dos lotes aprovados em uma data.

### 4.4 Armazenagem (Put-away)
1. Operador Logística bipa um palete `APROVADO_LOGISTICA` e o sistema identifica o lote.
2. Escolhe andar e corredor; os paletes do lote ocupam as primeiras posições livres (ordem de posição).
3. `[INVARIANTE]` Capacidade 1 por posição, garantida por `select_for_update` em `alocar_lote` e, nos bancos que suportam, por restrição única condicional (um `ARMAZENADO` por endereço).
4. Se o lote não couber inteiro em nenhum corredor/andar, a alocação vira **parcial**: preenche o corredor escolhido e o usuário escolhe outro para o restante.
5. **Retirar do mapa** (saída física, terminal): somente Operador Logística/Administradores, com confirmação.
6. A **reautenticação por senha do Gestor** e a sincronização com o SAP **[DESABILITADO]** nesta versão.

### 4.5 Consulta e gestão
- **Mapa de estoque:** visões por ocupação, andares, corredores, posições e geral; filtros por andar/corredor/posição/situação.
- **Consultar palete / lote:** situação, linha do tempo (quem e quando) e busca por lote ou data de fabricação.
- **Estatísticas** (gestores/admin): volumes e produtividade por período.
- **Usuários** (admin): busca no AD, criação, ativação e troca de perfil.

### 4.6 Fora do escopo atual
Outbox/integração de escrita no SAP B1, romaneio de carga e separação FIFO, e áreas de destino (`AreaDestino` existe no modelo, mas não é usada).

---

## 5. Estado dos Marcos

| Marco | Entrega | Situação |
|-------|---------|----------|
| M0–M1 | Fundação, autenticação e grupos | ✔ |
| M2 | Carga de endereços (`gerar_estrutura_armazem`: 1260 posições) | ✔ |
| M3 | Recebimento e impressão ZPL | ✔ |
| M4 | Put-away com controle de concorrência | ✔ |
| M5 | Transferência Produção/Logística, aprovação em lote, reprovação/correção, relatórios | ✔ |
| M6 | LDAP/AD, tela Usuários, estatísticas, consultas e mapa | ✔ |
| — | Reautenticação do Gestor, Outbox SAP, expedição | pendente / desabilitado |

---

## 6. Modelos (resumo)

- **Endereco:** `corredor` (A–T), `andar` (1–3), `posicao` (1–21), `codigo` (único), `capacidade` (1), `ativo`. Restrição única em (corredor, andar, posicao).
- **Produto:** `sku` (único), `descricao`, `ativo`, `peso_variavel`.
- **Palete:** `barcode_palete` (único), `sku`, `descricao`, `lote` (≤60), `peso_kg`, `data_fabricacao`, `estado`, `endereco` (`PROTECT`), datas de aprovação/armazenagem/retirada/reprovação, `motivo_reprovacao`, `reprovado_na_etapa`.
- **PaleteHistorico:** `palete`, `estado`, `usuario`, `criado_em` — uma linha por transição.
- **PerfilUsuario (core):** marca perfis atribuídos manualmente.
- **AreaDestino:** reservada, sem uso.
