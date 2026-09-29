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
- Instalar NSSM e registrar o serviço Windows do servidor de aplicação (reinício automático, start no boot) — comando exato depende de qual opção de HTTPS foi escolhida (ver linha abaixo).
- Configurar HTTPS — duas opções documentadas em [docs/https-setup.md](./https-setup.md): Opção A (Caddy como proxy reverso na frente do Waitress, `127.0.0.1:8000` não exposto, HTTPS na 443) ou Opção B (sem Caddy — `cheroot` serve HTTPS direto na própria porta 8000, para ambientes onde baixar o `.exe` do Caddy está bloqueado mas `pip install` funciona).
- Liberar no firewall do Windows: Opção A → 443/80 para a LAN (porta 8000 do Waitress não deve ser exposta fora do próprio servidor); Opção B → 8000 direto para a LAN.
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
- **HTTPS**: decidido — certificado autoassinado até a CA interna (`sitio-GOLD-CA`) voltar, em uma de duas opções (`docs/https-setup.md`): Opção A (Caddy como proxy reverso, `tls internal`, config em `deploy/Caddyfile`) ou Opção B (`cheroot` servindo HTTPS direto na porta 8000 via `deploy/serve_https.py` + `deploy/gerar_certificado.py`, usada no servidor de produção real porque o download do `caddy.exe` está bloqueado nessa VM mas `pip install` funciona). `[BLOQUEADOR]` ainda falta executar no servidor real: instalar dependências (`pip install -r requirements-prod.txt`), gerar o certificado, registrar o serviço NSSM (`WMS-Caddy` ou `WMS-HTTPS` conforme a opção), ativar `DJANGO_SECURE_SSL_REDIRECT` no `.env` real (`DJANGO_USE_X_FORWARDED_PROTO` só na Opção A), liberar a porta correspondente no firewall, e testar. Navegadores vão acusar certificado não confiável até a CA interna emitir um real ou o certificado raiz ser distribuído (`caddy trust` na Opção A, importação manual na Opção B, ambos + GPO) — decisão de UX aceita por ora.
- **IP fixo do servidor**: `DJANGO_ALLOWED_HOSTS` precisa de um IP ou hostname estável — se o servidor pegar IP por DHCP, reserve o IP no roteador/DHCP server antes de ir para produção, para não quebrar `ALLOWED_HOSTS` a cada reinício de rede.
- **Hostname `wms.cafedositio.com.br` (DNS interno) `[BLOQUEADOR — depende de TI/infra]`**: projeto já preparado para aceitar esse hostname além do IP (`DJANGO_ALLOWED_HOSTS`/`DJANGO_CSRF_TRUSTED_ORIGINS` no `.env`, certificado gerado com `python deploy\gerar_certificado.py 192.168.1.120,wms.cafedositio.com.br` — ver `docs/https-setup.md`). Falta criar o registro DNS de fato (`wms.cafedositio.com.br` → `192.168.1.120`) no servidor DNS/AD interno da empresa — isso é feito fora deste repositório (DNS Manager do Windows Server ou equivalente) por quem tiver acesso à infraestrutura de rede.
- **Cadastro de equipamentos**: `IMPRESSORA_ZPL_HOST`/`PORT` continuam sendo uma única impressora fixa via `.env` — não há suporte a múltiplas impressoras/múltiplos endereços físicos ainda, caso o armazém real precise disso.

## Verificação sugerida antes de declarar "pronto para produção"

- [ ] `python manage.py check --deploy` sem warnings relevantes rodando com o `.env` real de produção
- [ ] Login local funcionando com usuários reais (não os `_demo`)
- [ ] Impressão ZPL confirmada na impressora física do armazém
- [ ] Backup do MySQL testado (backup + restore, não só o agendamento)
- [ ] Serviço Windows (NSSM) sobrevive a um reboot do servidor
