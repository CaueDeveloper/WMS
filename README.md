# WMS — Sistema de Controle de Estoque por Lote

> **Projeto:** Café do Sítio  
> **Objetivo:** Sistema intermediário de controle de estoque na transição entre Produção e Logística  
> **Status:** Em desenvolvimento (Milestones M0–M6 implementados)

## 📋 Visão Geral

O WMS é uma camada de controle de estoque que **não substitui o SAP B1**, mas funciona como intermediária na transição entre os departamentos de Produção e Logística. O sistema gerencia:

- **Entrada de paletes** com validação de SKU e lote
- **Armazenagem com mapa visual** em grade fixa de endereços
- **Separação por romaneio** para expedição
- **Impressão de etiquetas** em impressoras Zebra ZT421

### Características Principais

✅ Autenticação nativa Django com grupos (`Administradores`, `Gestores`, `Operadores`)  
✅ Modelo de palete com estados (`CRIADO` → `EM_TRANSFERENCIA` → `CONFERIDO` → `ARMAZENADO` → `FINALIZADO`)  
✅ Endereçamento em grade (Corredores 01–20 × Andares A/B/C = 60 locais)  
✅ Capacidade rígida por endereço (21 paletes) com proteção contra condição de corrida  
✅ Impressão assíncrona via ZPL, disparada imediatamente na entrada (Zebra DataWedge em modo emulação de teclado)  
✅ Aprovação em lote pelo Gestor  
✅ Reautenticação por senha do Gestor antes de finalizar guardas  

## 🛠️ Stack Técnico

| Componente | Tecnologia |
|-----------|-----------|
| **Backend** | Python 3.10+ / Django 4.2+ |
| **Banco de Dados** | SQLite (dev) / MySQL (prod) |
| **Frontend** | HTML5 + CSS3 (sem SPA) |
| **Impressão** | Zebra ZT421 via ZPL (TCP/IP porta 9100) |
| **Coleta** | Zebra DataWedge em emulação de teclado |
| **Autenticação** | Django Auth nativo + `auth.Group` |

## 📁 Estrutura do Projeto

```
WMS2/
├── wms/                          # Projeto Django principal
│   ├── settings.py              # Configuração (DATABASES, INSTALLED_APPS)
│   ├── urls.py                  # Roteamento principal
│   ├── wsgi.py / asgi.py        # Servidores
│   └── __init__.py
│
├── apps/                         # Aplicativos Django
│   ├── core/                    # Autenticação, grupos, decoradores
│   │   ├── decorators.py       # @group_required
│   │   ├── views.py            # Login, logout
│   │   ├── models.py           # (sem modelos específicos)
│   │   ├── migrations/
│   │   │   └── 0001_popular_grupos.py  # Popula Administradores, Gestores, Operadores
│   │   └── templates/          # base.html, login.html, home.html
│   │
│   ├── estoque/                # Armazenagem, put-away, mapa visual
│   │   ├── models.py           # Endereco, Produto, AreaDestino
│   │   ├── services.py         # alocar_palete (transação + lock)
│   │   ├── views.py            # putaway (2 etapas), mapa_estoque
│   │   ├── admin.py            # Interface admin para Endereco, AreaDestino
│   │   ├── management/commands/
│   │   │   └── gerar_estrutura_armazem.py  # Cria 60 endereços (idempotente)
│   │   └── templates/estoque/  # putaway.html, mapa_estoque.html
│   │
│   └── recebimento/            # Entrada, transferência, impressão ZPL
│       ├── models.py           # Palete
│       ├── views.py            # receber_palete, transferencia_gestor
│       ├── services.py         # aprovar_lote (transação + lock)
│       ├── forms.py            # RecebimentoForm
│       ├── zpl.py              # disparar_impressao_async (socket TCP/IP)
│       └── templates/recebimento/  # receber_palete.html, transferencia_gestor.html
│
├── docs/                        # Documentação
│   ├── PRD.md                  # Product Requirements Document
│   └── escopo.md               # Escopo e análise técnica
│
├── manage.py                   # Comando de gerenciamento Django
├── requirements.txt            # Dependências (dev)
├── requirements-prod.txt       # Dependências (produção)
├── CLAUDE.md                   # Guia para Claude Code
└── .gitignore                  # Exclusões Git
```

## 🚀 Como Começar

### Pré-requisitos

- Python 3.10+
- pip ou pipenv
- SQLite (padrão) ou MySQL (produção)
- Git

### Instalação

```bash
# Clonar o repositório
git clone https://github.com/CaueSilvS/WMS.git
cd WMS2

# Criar ambiente virtual
python -m venv venv
source venv/bin/activate  # Linux/macOS
# ou
venv\Scripts\activate  # Windows

# Instalar dependências
pip install -r requirements.txt

# Aplicar migrações
python manage.py migrate

# Criar estrutura de armazém (60 endereços)
python manage.py gerar_estrutura_armazem

# Criar usuários de demonstração (opcional)
python manage.py criar_usuarios_demo

# Executar servidor de desenvolvimento
python manage.py runserver
```

Acesse `http://localhost:8000/` no navegador.

## 📱 Testar com um Coletor Físico (Zebra MC2700/TC52)

Por padrão o servidor só aceita conexões vindas da própria máquina (`localhost`). Para testar em um coletor real na mesma rede Wi-Fi:

1. **Descubra o IP da máquina de desenvolvimento** (Windows: `ipconfig`, procure o adaptador conectado à rede — ex.: `10.61.1.193`).
2. **Confirme que esse IP está em `ALLOWED_HOSTS`** em `wms/settings.py` (ajuste se a máquina reconectou e o IP mudou).
3. **Suba o servidor escutando em todas as interfaces**, não só localhost:
   ```bash
   python manage.py runserver 0.0.0.0:8000
   ```
4. **Libere a porta no Firewall do Windows**, se solicitado pelo prompt "Permitir acesso" na primeira execução.
5. **No coletor**, configure o perfil do Zebra DataWedge em modo *Keystroke/HID output* apontando para o navegador, e acesse `http://<IP-da-máquina>:8000/` (ex.: `http://10.61.1.193:8000/`).
6. **Faça login** com um dos usuários de demonstração (`operador` / `Operador@123` ou `gestor` / `Gestor@123`).

> ⚠️ O campo **Produto** na tela de recebimento (`/recebimento/receber/`) é um menu suspenso (`<select>`), não um campo de texto — bipar um código de barras nele não funciona de forma confiável. O teste real de bipagem contínua vale para os campos de texto de outras telas (ex.: **Sequencial** na entrada). O Put-away (`/estoque/putaway/`) não usa mais bipagem — passou a ser operado por tablet/celular, com o número do lote digitado manualmente.

## 🔄 Fluxo de Trabalho

### 1️⃣ Recebimento (Colaborador)

**Rota:** `/recebimento/receber/`

Colaborador envia dados do palete:
- SKU
- Lote
- Descrição
- Peso

**Validação:** Nenhuma — o objetivo do projeto é nunca ter paletes divergentes, então qualquer peso é aceito.  
**Resultado:** Palete em estado `CRIADO` e a etiqueta é impressa **imediatamente** na Zebra ZT421, sem esperar aprovação do Gestor.

### 2️⃣ Transferência (Gestor)

**Rota:** `/recebimento/transferencia/`  
**Permissão:** Grupo `Gestores`

Gestor bipa com o coletor cada palete impresso:
- Cada bipagem move o palete `CRIADO` → `EM_TRANSFERENCIA`, entrando numa fila organizada por `lote`.
- Não há rejeição manual individual.

Quando todos os paletes do lote (mesmo SKU/dia) estiverem na fila, o Gestor **aprova o lote inteiro de uma vez**, escolhendo uma única `AreaDestino`:
- Estado `EM_TRANSFERENCIA` → `CONFERIDO` para todos os paletes do lote.

### 3️⃣ Put-away (Gestor de Logística)

**Rota:** `/estoque/putaway/`  
**Permissão:** Nenhuma restrição de grupo — aberta a qualquer usuário autenticado; operada por tablet/celular (não usa o coletor Zebra)

**Etapa A:** Gestor digita o número do lote (ou clica em "Armazenar" na lista de lotes pendentes)  
→ Sistema mostra SKU/descrição/quantidade de paletes/peso total do lote

**Etapa B:** Gestor clica no endereço disponível na grade  
→ Sistema aloca **todos os paletes do lote de uma vez** naquele endereço (tudo ou nada; rejeita se não houver vagas suficientes)

**Resultado:** Todos os paletes do lote em estado `ARMAZENADO`

### 4️⃣ Confirmação Final (Gestor)

**Rota:** `/armazenagem/confirmar/`  
**Permissão:** Reautenticação por senha

Gestor revisa paletes `ARMAZENADO` e confirma com sua senha:
- Senha correta → Estado `FINALIZADO` (sincronizado no SAP B1)
- Senha errada → Rejeita a confirmação

## 🔒 Regras de Negócio Invariantes

| Regra | Descrição |
|-------|-----------|
| **SAP B1 é a fonte única de saldo** | WMS nunca altera saldo direto; apenas via Outbox |
| **Capacidade rígida de endereço** | Máximo 21 paletes por endereço; proteção contra race condition |
| **Reautenticação obrigatória** | Confirmação final exige senha do Gestor |
| **Impressão imediata na entrada** | Etiqueta imprime assim que o palete é criado, sem validação nem aprovação prévia — objetivo do projeto é nunca ter divergências |
| **Aprovação em lote** | Gestor aprova todos os paletes `EM_TRANSFERENCIA` de um mesmo `lote` de uma vez, com `.select_for_update()` |

## 🗂️ Modelos de Dados

### `Endereco` (apps/estoque/models.py)

```python
class Endereco(models.Model):
    codigo = models.CharField(max_length=10, unique=True)  # ex: "04-B"
    capacidade_total = models.IntegerField(default=21)
    ocupacao_atual = models.IntegerField(default=0)
    ativo = models.BooleanField(default=True)
```

### `Palete` (apps/recebimento/models.py)

```python
class Palete(models.Model):
    ESTADOS = [
        ('CRIADO', 'Criado'),
        ('EM_TRANSFERENCIA', 'Em Transferência'),
        ('CONFERIDO', 'Conferido'),
        ('ARMAZENADO', 'Armazenado'),
        ('FINALIZADO', 'Finalizado'),
    ]
    barcode_palete = models.CharField(max_length=50, unique=True)
    sku = models.CharField(max_length=50)
    lote = models.CharField(max_length=50)
    descricao = models.TextField()
    peso = models.DecimalField(max_digits=10, decimal_places=2)
    estado = models.CharField(max_length=20, choices=ESTADOS, default='CRIADO')
    endereco = models.ForeignKey('estoque.Endereco', null=True, blank=True)
    area_destino = models.ForeignKey('estoque.AreaDestino', null=True, blank=True)
    data_criacao = models.DateTimeField(auto_now_add=True)
```

## 📊 Estados do Palete

```
CRIADO → (disparar_impressao_async, imediato)
  ↓ (Gestor bipa no coletor)
EM_TRANSFERENCIA
  ↓ (Gestor aprova o lote inteiro)
CONFERIDO
  ↓ (Gestor de Logística faz o putaway)
ARMAZENADO
  ↓ (Gestor confirma com senha)
FINALIZADO
```

## 🖨️ Impressão ZPL

A impressão é disparada de forma **assíncrona** em thread daemon via `apps/recebimento/zpl.py::disparar_impressao_async`:

- **Impressora:** Zebra ZT421
- **Protocolo:** TCP/IP bruto (porta 9100)
- **Host:** Configurável via `IMPRESSORA_ZPL_HOST` em `settings.py`
- **Bloqueio:** A resposta HTTP não aguarda a impressão

```python
# Exemplo de ativação
disparar_impressao_async(palete_id=123)
```

## 🔐 Autenticação e Grupos

Três grupos pré-populados:

| Grupo | Permissões |
|-------|-----------|
| **Administradores** | Acesso total (admin, todas as views) |
| **Gestores** | Transferência (bipagem + aprovação de lote), confirmação final |
| **Operadores** | Recebimento; put-away é aberto a qualquer usuário autenticado |

Use o decorator `@group_required('NomeGrupo')` para proteger views.

## ⚙️ Configuração

### `wms/settings.py`

**Banco de dados:**
```python
# Desenvolvimento (SQLite)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Produção (MySQL) — descomente
# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.mysql',
#         'NAME': 'wms_db',
#         'USER': 'root',
#         'PASSWORD': '...',
#         'HOST': 'localhost',
#         'PORT': '3306',
#     }
# }
```

**Impressora Zebra:**
```python
IMPRESSORA_ZPL_HOST = '192.168.1.100'  # IP da impressora
IMPRESSORA_ZPL_PORT = 9100              # Porta TCP/IP
```

## 📚 Documentação Adicional

- **[PRD.md](docs/PRD.md)** — Product Requirements Document (requisitos detalhados)
- **[escopo.md](docs/escopo.md)** — Escopo técnico e análise de arquitetura
- **[CLAUDE.md](CLAUDE.md)** — Guia para Claude Code (instruções de desenvolvimento)

## 🤝 Contribuindo

1. Faça um fork do repositório
2. Crie uma branch para sua feature (`git checkout -b feature/MinhaFeature`)
3. Commit suas mudanças (`git commit -am 'Adiciona MinhaFeature'`)
4. Push para a branch (`git push origin feature/MinhaFeature`)
5. Abra um Pull Request

## 📞 Contato

- **Desenvolvedor:** Cauê Silva ([@CaueSilvS](https://github.com/CaueSilvS))

## 📄 Licença

Este projeto é propriedade da Café do Sítio. Todos os direitos reservados.

---

**Status dos Milestones:**

- [x] **M0** — Fundação (projeto + apps)
- [x] **M1** — Autenticação e grupos
- [x] **M2** — Carga inicial de endereços
- [x] **M3** — Módulo de entrada (recebimento + impressão)
- [x] **M4** — Put-away e concorrência
- [x] **M5** — Impressão imediata, transferência por bipagem e aprovação em lote
- [x] **M6** — Remoção do estado DIVERGENTE (peso sempre aceito, sem bloqueio de fluxo)

**Próximas etapas:** Integração com SAP B1 (Outbox), testes em produção, otimizações de performance.
