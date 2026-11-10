# WMS — Controle de Estoque por Lote

> **Cliente:** Café do Sítio
> **Objetivo:** camada intermediária de controle de estoque entre Produção e Logística. **Não substitui o SAP B1**, que segue como fonte única de saldo.
> Requisitos detalhados em [docs/PRD.md](docs/PRD.md).

## Funcionalidades

- Emissão de etiquetas de palete (ZPL, Zebra ZT421) com lote, peso e data de fabricação
- Transferência Produção → Logística com bipagem e aprovação em lote por dois gestores
- Reprovação, correção pelo Gestor e reimpressão de etiqueta quando peso/data mudam
- Put-away com alocação nas primeiras posições livres, inclusive parcial
- Mapa de estoque (1260 posições), consulta de palete/lote, retirada
- Histórico completo de cada palete, relatórios de aprovação e estatísticas
- Login pelo Active Directory; perfis geridos na tela Usuários

## Stack

| Componente | Tecnologia |
|-----------|-----------|
| Backend | Python 3.12+ / Django 6.0 |
| Banco | SQLite (dev) / MySQL (produção, `DJANGO_DB_*`) |
| Frontend | HTML5 + Tailwind compilado (sem SPA) |
| Impressão | Zebra ZT421, ZPL via TCP (porta 9100) |
| Coleta | Zebra DataWedge em emulação de teclado |
| Autenticação | `django-auth-ldap` (AD) + `auth.Group` |
| Servidor | `cheroot` com TLS direto (`deploy/serve_https.py`) |

## Estrutura

```
wms/              projeto Django (settings, urls)
apps/core/        login, grupos, menu (navegacao.py), home, estatísticas, usuários/AD
apps/logistica/     Endereco, Produto, put-away, mapa, consultas
apps/producao/ Palete, PaleteHistorico, emissão, transferência, relatórios, ZPL
deploy/           HTTPS (cheroot), geração de certificado, Tailwind
docs/             PRD e escopo
```

## Instalação (desenvolvimento)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python manage.py migrate
python manage.py gerar_estrutura_armazem   # cria as 1260 posições (idempotente)
python manage.py criar_usuarios_demo       # opcional
python manage.py runserver
python manage.py test                      # suíte de testes
```

### Configuração (`.env` na raiz, não versionado)

| Variável | Uso |
|----------|-----|
| `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` | Django |
| `DJANGO_SECURE_SSL_REDIRECT` | força HTTPS (só com o servidor TLS direto) |
| `DJANGO_DB_NAME/USER/PASSWORD/HOST/PORT` | MySQL; sem `DJANGO_DB_NAME` usa SQLite |
| `IMPRESSORA_ZPL_HOST`, `IMPRESSORA_ZPL_PORT` | impressora (porta padrão 9100; sem host a etiqueta não é enviada) |
| `AUTH_LDAP_*` | servidor, domínio, bases de busca e grupos do AD |

No MySQL, as consultas por data (`__date`) exigem as tabelas de fuso carregadas (`mysql_tzinfo_to_sql`).

Usuários demo (`criar_usuarios_demo`): `admin_demo`, `operador_producao`, `gestor_producao`, `operador_logistica`, `gestor_logistica` — senhas no próprio comando. **Não usar em produção.** O `/admin` exige superusuário (`createsuperuser`).

### Testar com coletor

Suba com `python manage.py runserver 0.0.0.0:8000`, inclua o IP da máquina em `DJANGO_ALLOWED_HOSTS`, libere a porta no firewall e acesse pelo navegador do coletor. Configure o DataWedge em modo teclado com `Enter` ao final. O campo Produto é um `<select>`; a bipagem contínua vale para os campos de texto (lote/código de palete).

## Telas e perfis

| Tela | Rota | Perfis |
|------|------|--------|
| Produção (emitir etiquetas) | `/recebimento/receber/` | Operador Produção |
| Transferência Produção | `/recebimento/transferencia/` | Operador (bipa/reprova), Gestor Produção (aprova/corrige/reprova) |
| Transferência Logística | `/recebimento/transferencia-logistica/` | Operador (bipa/reprova), Gestor Logística (aprova/reprova) |
| Put-away | `/estoque/putaway/` | Operador Logística |
| Mapa de estoque / Consultas | `/estoque/mapa/`, `/estoque/consultar/` | Todos os operacionais (retirar: só Operador Logística) |
| Estatísticas | `/estatisticas/` | Gestores e Administradores |
| Usuários | `/usuarios/` | Administradores |

Administradores acessam tudo. Novos usuários de Produção/Logística nascem inativos até um Administrador ativá-los.

## Fluxo do palete

```
FABRICADO → TRANSFERENCIA_PRODUCAO → APROVADO_PRODUCAO → TRANSFERENCIA_LOGISTICA → APROVADO_LOGISTICA → ARMAZENADO → RETIRADO
   ▲           (qualquer etapa anterior) → REPROVADO ── corrigir (Gestor Produção) ──┘ volta a FABRICADO
```

A sincronização no SAP (status `FINALIZADO`, removido) e a reautenticação por senha do Gestor estão **desabilitados** nesta versão.

## Regras importantes

- Capacidade de **1 palete por posição**, com `select_for_update` na alocação e restrição única condicional no banco (exceto MySQL).
- Lote: apenas `A-Z 0-9 . _ / -`; não recebe novos paletes depois de aprovado.
- Cada transição de estado grava histórico (usuário e horário). Palete e histórico são somente leitura no `/admin`.
- Impressão assíncrona, disparada após o commit; falha de impressora é só registrada em log — confira a etiqueta física.
- Corrigir peso ou data de um palete reprovado reimprime a etiqueta.

## Impressão ZPL

`apps/producao/zpl.py`: o layout vive em `templates_zpl/etiqueta_palete.zpl` (placeholders `[[BARCODE]]`, `[[LOTE]]`, `[[PESO]]`, `[[DATA]]`, `[[HORA]]`); a etiqueta de endereço fica em `apps/logistica/templates_zpl/`. Teste a conexão com `python manage.py testar_impressora`.

## Estado do projeto

- [x] Fundação, autenticação e grupos, carga de endereços
- [x] Recebimento e impressão
- [x] Put-away com concorrência
- [x] Transferência em duas etapas, reprovação/correção, relatórios
- [x] LDAP/AD, usuários, estatísticas, consultas e mapa
- [ ] Reautenticação do Gestor / `FINALIZADO` (desabilitado)
- [ ] Integração SAP B1 (Outbox) e expedição por romaneio

Desenvolvedor: Cauê Silva ([@CaueSilvS](https://github.com/CaueSilvS)). Propriedade da Café do Sítio; todos os direitos reservados.
