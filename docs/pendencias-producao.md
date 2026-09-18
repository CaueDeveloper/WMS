# Pendências para Produção

> Checklist do que falta para colocar o WMS no ar em produção (servidor Windows on-premise, MySQL). Atualizado conforme o trabalho avança — ver também [CLAUDE.md](../CLAUDE.md) para o histórico de milestones funcionais (M0–M25).

## Já feito nesta sessão de hardening

- `wms/settings.py`: `DEBUG` com default seguro (`False`), `SECRET_KEY` obrigatória quando `DEBUG=False` (falha alto e cedo em vez de subir insegura), `ALLOWED_HOSTS` configurável via `DJANGO_ALLOWED_HOSTS`, switch SQLite/MySQL via `DJANGO_DB_*`, `STATIC_ROOT`/`STORAGES` para Whitenoise, settings de segurança condicionais (`CSRF_TRUSTED_ORIGINS`, `SECURE_SSL_REDIRECT`, HSTS) desligados por padrão (tráfego é HTTP interno na LAN).
- `requirements-prod.txt`: adicionado `waitress` (servidor WSGI para Windows) e `whitenoise` (estáticos sem precisar de Nginx/IIS).
- `.env.example`: documentadas todas as variáveis que faltavam (`DJANGO_ALLOWED_HOSTS`, `DJANGO_DB_*`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DJANGO_SECURE_SSL_REDIRECT`).
- Validado nesta máquina: `manage.py check --deploy`, `manage.py collectstatic`, e a aplicação respondendo via `waitress-serve` real (HTTP 302 correto para rota protegida).

## Pendências

### 1. Executar o runbook no servidor real `[BLOQUEADOR]`
Nada abaixo foi feito de fato — só preparado no repositório. Falta, na máquina Windows que vai hospedar o sistema:
- Instalar Python 3.12+, Git e MySQL Server (ou apontar para um MySQL já existente na rede).
- Clonar o repositório, criar venv, `pip install -r requirements-prod.txt`.
- Copiar `.env.example` → `.env` e preencher os valores reais: `DJANGO_SECRET_KEY` (gerar nova, nunca reaproveitar a de dev), `DJANGO_DEBUG=False`, `DJANGO_ALLOWED_HOSTS`, credenciais MySQL, `IMPRESSORA_ZPL_HOST/PORT`.
- Criar banco/usuário MySQL, rodar `manage.py migrate`, `manage.py gerar_estrutura_armazem`, criar usuários reais (`createsuperuser` + grupos, em vez do comando `criar_usuarios_demo`, que é só para desenvolvimento).
- `manage.py collectstatic`.
- Instalar NSSM e registrar um serviço Windows rodando `waitress-serve --host=0.0.0.0 --port=8000 wms.wsgi:application` dentro da venv (reinício automático, start no boot).
- Liberar a porta escolhida no firewall do Windows para a LAN do armazém.
- Testar de ponta a ponta a partir do servidor real: bipagem pelo coletor Zebra, impressão ZPL na impressora física.
- Configurar rotina de backup do MySQL (`mysqldump` agendado via Task Scheduler).

### 2. Credenciais expostas no histórico do Git `[BLOQUEADOR — segurança]`
Identificado desde o M11 (`CLAUDE.md`) e ainda não resolvido: `SECRET_KEY` antiga e as credenciais Z-API que existiram no projeto (incluindo o número de WhatsApp do Gestor de Logística, PII) ficaram em texto plano no histórico do repositório antes do hardening de variáveis de ambiente — a integração Z-API foi removida do código, mas isso não apaga o que já está no histórico do Git. Antes de ir para produção:
- Gerar uma `SECRET_KEY` nova (não reaproveitar nenhuma que já apareceu no histórico).
- Se a conta Z-API antiga ainda existir, revogar/regenerar as credenciais no painel Z-API mesmo assim, já que ficaram expostas.
- Avaliar se vale reescrever o histórico do Git (ou, se o repositório for privado e o risco for aceitável, só girar as credenciais mesmo).

### 3. Suíte de testes removida
Os `tests.py` de todos os apps foram removidos do repositório — não há mais suíte automatizada para validar autenticação/grupos, concorrência de armazenagem, ou o fluxo de recebimento/transferência. Antes de produção, avaliar se vale reconstruir cobertura mínima para os pontos críticos (capacidade de endereço com `select_for_update`, aprovação de lote, permissões por grupo).

### 4. LDAP/Active Directory nunca testado contra um AD real
`django-auth-ldap` está integrado (`wms/settings.py`) mas nunca foi validado contra um servidor AD de verdade — `python-ldap` não tem wheel oficial para Windows, então isso só é testável num host Linux/WSL2 com AD real (nota do M18 em `CLAUDE.md`). Além disso, os nomes de grupo reais no AD (atributo `cn`) precisam bater exatamente com os 5 grupos usados pelo sistema (`Administradores`, `Operador Produção`, `Operador Logística`, `Gestor Produção`, `Gestor Logística`); se não baterem, é necessário um passo de mapeamento adicional ainda não implementado. Enquanto isso não for validado, produção deve rodar só com autenticação local (`ModelBackend`).

### 5. Integração SAP B1 / Outbox ainda não existe
O invariante do PRD de que o mapa de put-away só deve liberar um palete após a transferência Produção→Logística estar confirmada no SAP B1 não tem integração real — hoje é um proxy manual (etapa 4 do fluxo em `CLAUDE.md`, telas de conferência da Logística). Não bloqueia a operação atual, mas é uma divergência entre o PRD e a implementação real que deve ficar registrada.

### 6. Decisões de infraestrutura que ainda dependem de escolha operacional
- **HTTPS**: hoje desligado por padrão (`DJANGO_SECURE_SSL_REDIRECT=False`), assumindo tráfego HTTP puro dentro da LAN do armazém. Se em algum momento houver acesso remoto ou exigência de criptografia em trânsito, será necário decidir entre terminar TLS num proxy (Nginx/IIS) na frente do Waitress ou outra abordagem, e então ativar os settings de segurança já preparados (`SECURE_SSL_REDIRECT`, HSTS, cookies seguros).
- **IP fixo do servidor**: `DJANGO_ALLOWED_HOSTS` precisa de um IP ou hostname estável — se o servidor pegar IP por DHCP, reserve o IP no roteador/DHCP server antes de ir para produção, para não quebrar `ALLOWED_HOSTS` a cada reinício de rede.
- **Cadastro de equipamentos**: `IMPRESSORA_ZPL_HOST`/`PORT` continuam sendo uma única impressora fixa via `.env` — não há suporte a múltiplas impressoras/múltiplos endereços físicos ainda, caso o armazém real precise disso.

## Verificação sugerida antes de declarar "pronto para produção"

- [ ] `python manage.py check --deploy` sem warnings relevantes rodando com o `.env` real de produção
- [ ] Login local funcionando com usuários reais (não os `_demo`)
- [ ] Impressão ZPL confirmada na impressora física do armazém
- [ ] Backup do MySQL testado (backup + restore, não só o agendamento)
- [ ] Serviço Windows (NSSM) sobrevive a um reboot do servidor
