# WMS — Sistema de Controle de Estoque por Lote

> **Projeto:** Café do Sítio  
> **Objetivo:** Sistema intermediário de controle de estoque na transição entre Produção e Logística  
> **Status:** Em desenvolvimento (Milestones M0–M4 implementados)

## 📋 Visão Geral

O WMS é uma camada de controle de estoque que **não substitui o SAP B1**, mas funciona como intermediária na transição entre os departamentos de Produção e Logística. O sistema gerencia:

- **Entrada de paletes** com validação de SKU e lote
- **Armazenagem com mapa visual** em grade fixa de endereços
- **Separação por romaneio** com controle de divergências
- **Impressão de etiquetas** em impressoras Zebra ZT421

### Características Principais

✅ Autenticação nativa Django com grupos (`Administradores`, `Gestores`, `Operadores`)  
✅ Modelo de palete com estados (`CRIADO` → `CONFERIDO` → `ARMAZENADO` → `FINALIZADO`)  
✅ Endereçamento em grade (Corredores 01–20 × Andares A/B/C = 60 locais)  
✅ Capacidade rígida por endereço (21 paletes) com proteção contra condição de corrida  
✅ Impressão assíncrona via ZPL (Zebra DataWedge em modo emulação de teclado)  
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
│   │   ├── views.py            # Login, logout, relatorio_divergencias
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
│   │   ├── tests.py            # Testes de concorrência e validação
│   │   └── templates/estoque/  # putaway.html, mapa_estoque.html
│   │
│   └── recebimento/            # Entrada, conferência, impressão ZPL
│       ├── models.py           # Palete, ContadorLote
│       ├── views.py            # receber_palete, conferencia_gestor
│       ├── forms.py            # RecebimentoForm
│       ├── zpl.py              # disparar_impressao_async (socket TCP/IP)
│       ├── tests.py            # Testes de ZPL, permissões, fluxos
│       └── templates/recebimento/  # receber_palete.html, conferencia_gestor.html
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

## 🔄 Fluxo de Trabalho

### 1️⃣ Recebimento (Colaborador)

**Rota:** `/recebimento/receber/`

Colaborador envia dados do palete:
- SKU
- Lote
- Descrição
- Peso

**Validação:** Peso `<= 0` → Estado `DIVERGENTE` (trava)  
**Resultado:** Palete em estado `CRIADO` (aguardando conferência)

### 2️⃣ Conferência (Gestor)

**Rota:** `/recebimento/conferencia/`  
**Permissão:** Grupo `Gestores`

Gestor analisa paletes `CRIADO` e:
- **Aprova** + escolhe `AreaDestino` → Estado `CONFERIDO` + dispara impressão ZPL
- **Rejeita** → Estado `DIVERGENTE`

Neste momento, a etiqueta é impressa na Zebra ZT421.

### 3️⃣ Put-away (Colaborador)

**Rota:** `/estoque/putaway/`

**Etapa A:** Bipador lê o barcode do palete  
→ Sistema mostra SKU/descrição/lote/peso

**Etapa B:** Colaborador bipa/digita o código do endereço  
→ Sistema aloca o palete (ou rejeita se endereço cheio)

**Resultado:** Palete em estado `ARMAZENADO`

### 4️⃣ Confirmação Final (Gestor)

**Rota:** `/relatorios/divergencias/`  
**Permissão:** Reautenticação por senha

Gestor revisa paletes `ARMAZENADO` e confirma com sua senha:
- Senha correta → Estado `FINALIZADO` (sincronizado no SAP B1)
- Senha errada → Rejeita a confirmação

## 🔒 Regras de Negócio Invariantes

| Regra | Descrição |
|-------|-----------|
| **SAP B1 é a fonte única de saldo** | WMS nunca altera saldo direto; apenas via Outbox |
| **Palete divergente trava o fluxo** | SKU/Lote inválido ou inconsistência grave → `DIVERGENTE` |
| **Capacidade rígida de endereço** | Máximo 21 paletes por endereço; proteção contra race condition |
| **Reautenticação obrigatória** | Confirmação final exige senha do Gestor |
| **Impressão após aprovação** | Etiqueta só imprime quando Gestor aprova, não no envio |

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
        ('CONFERIDO', 'Conferido'),
        ('ARMAZENADO', 'Armazenado'),
        ('FINALIZADO', 'Finalizado'),
        ('DIVERGENTE', 'Divergente'),
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
CRIADO
  ↓ (Gestor aprova)
CONFERIDO → (disparar_impressao_async)
  ↓
ARMAZENADO (após putaway)
  ↓ (Gestor confirma com senha)
FINALIZADO

OU em qualquer etapa:
DIVERGENTE (erro de validação ou rejeição)
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

## 🧪 Testes

Rode os testes com:

```bash
# Todos os testes
python manage.py test

# Testes específicos
python manage.py test apps.core.tests
python manage.py test apps.estoque.tests
python manage.py test apps.recebimento.tests

# Testes com verbosidade
python manage.py test --verbosity=2
```

### Cobertura de Testes

- ✅ Autenticação e grupos (`core/tests.py`)
- ✅ Alojamento e concorrência (`estoque/tests.py`)
- ✅ Validação de palete e ZPL (`recebimento/tests.py`)

## 🔐 Autenticação e Grupos

Três grupos pré-populados:

| Grupo | Permissões |
|-------|-----------|
| **Administradores** | Acesso total (admin, todas as views) |
| **Gestores** | Conferência, aprovação, confirmação final |
| **Operadores** | Recebimento, put-away |

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

Todas as mudanças devem ser testadas via `python manage.py test` antes de enviar.

## 📞 Contato

- **Desenvolvedor:** Cauê Silva ([@CaueSilvS](https://github.com/CaueSilvS))
- **Email:** sluizcampos2019@gmail.com

## 📄 Licença

Este projeto é propriedade da Café do Sítio. Todos os direitos reservados.

---

**Status dos Milestones:**

- [x] **M0** — Fundação (projeto + apps)
- [x] **M1** — Autenticação e grupos
- [x] **M2** — Carga inicial de endereços
- [x] **M3** — Módulo de entrada (recebimento + impressão)
- [x] **M4** — Put-away e concorrência

**Próximas etapas:** Integração com SAP B1 (Outbox), testes em produção, otimizações de performance.
