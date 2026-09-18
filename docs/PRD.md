# PRD — Projeto WMS (Controle de Estoque por Lote)

> **Documento de Requisitos de Produto — versão executável por Claude Code**
> Indústria de Café · Operação Logística · Café do Sítio
> Stack: Python/Django + SQLite (Dev) / MySQL (Prod) + HTML5/CSS3
> Status: Baseline Aprovado

---

## 0. Instruções para o Claude Code

Este PRD é a **fonte única de verdade** do projeto. Antes de gerar qualquer código:
1. Leia integralmente as seções de 1 a 6 (contexto, decisões, modelos e contratos).
2. Gere o arquivo `CLAUDE.md` na raiz do repositório utilizando estritamente a base da **Seção 7**.
3. Execute o projeto estritamente milestone por milestone (**Seção 5**). Não pule etapas. Cada milestone deve ser um incremento testável via `python manage.py test`.
4. Toda premissa marcada como `[ASSUMPTION]` representa uma decisão técnica adotada para o desenvolvimento.
5. Toda regra marcada como `[INVARIANTE]` é uma regra de negócio absoluta e nunca pode ser violada por nenhuma camada do software.

---

## 1. Contexto e Problema

### 1.1 Objetivo Geral
Implantar o **controle de estoque por lote** na transição entre os setores de Produção e Logística da Café do Sítio. O sistema atuará como uma camada de controle intermediária para registrar, classificar e validar a movimentação de mercadorias ponta a ponta (entrada, armazenagem com mapa visual e separação de saída por romaneio), garantindo rastreabilidade e integridade sem substituir o SAP B1 como fonte oficial de saldo.

### 1.2 Problema Atual
A operação atual depende de processos manuais (pesagem manual, conferências informadas via grupos de WhatsApp) e carece de classificação automatizada na transferência de responsabilidade entre as áreas de armazenagem e distribuição. Isso gera gargalos operacionais, riscos de erros humanos e imprecisão crônica na rastreabilidade dos lotes de café.

---

## 2. Decisões de Arquitetura

| # | Tema | Decisão do Projeto | Implicação Técnica / Implementação |
|---|------|---------------------|-----------------------------------|
| **D1** | Fonte de Saldo | **SAP B1 é a única fonte.** O WMS gerencia o fluxo físico e reservas. | O WMS nunca altera saldo arbitrariamente; envia requisições estruturadas ao ERP. |
| **D2** | Persistência | **Django ORM nativo.** SQLite3 em Desenvolvimento e MySQL em Produção. | Portabilidade total. A virada de banco ocorre apenas alterando o bloco `DATABASES` no `settings.py`. |
| **D3** | Interface | **Views Django Tradicionais (HTML5 + CSS3 customizado).** | Interface leve e responsiva. Sem SPAs pesadas (React/Node). Foco em desempenho nos coletores. |
| **D4** | Hardware Coleta | Coletores **Zebra MC2700 e TC52** via DataWedge (Emulação de teclado). | O layout CSS deve ser otimizado para o pior caso de tela: **Zebra MC2700 (4" - 800×480)**. |
| **D5** | Impressão | Impressora térmica industrial **Zebra ZT421** via linguagem **ZPL**. | O backend Django abre um Socket TCP/IP na porta `9100` e envia a string ZPL diretamente para o IP da impressora. |
| **D6** | Endereçamento | Grade de **Guardas (01-20) × Andares (A, B, C) × Lados (01, 02)**. | Total de 120 endereços físicos, código de 5 segmentos `001-LL-A-GG-01` (ex.: `001-02-A-20-01`). **Capacidade máxima rígida de 21 paletes por endereço**. |
| **D7** | Autenticação | **Django Auth System** com controle por Grupos (`auth.Group`). | Permissões controladas via decorators (`@permission_required`) ou mixins nas Views. |
| **D8** | Notificação `[REMOVIDO]` | ~~Z-API (WhatsApp Business API) avisa o Gestor de Logística quando um lote é aprovado.~~ | Integração removida do projeto — decisão de não usar mais o Z-API. O aviso à Logística volta a ser manual. |

### 2.1 Premissas Explícitas `[ASSUMPTION]`
* `A1`: O coletor Zebra opera em modo online contínuo via Wi-Fi interno da empresa.
* `A2`: As leituras do scanner Zebra disparam um evento de `Enter` automático, permitindo o fluxo de bipagem contínua sem cliques manuais na tela.
* `A3`: O reprocessamento de erros de integração com o SAP será feito por meio de uma tabela de log de comandos (Outbox) monitorada internamente.

---

## 3. Personas e Papéis (Django Grupos / RBAC)

O controle de acessos será gerenciado nativamente pelo sistema de permissões do Django:

* **ADMIN (`Superuser`):** Acesso irrestrito ao painel administrativo do Django (`/admin`), configuração de parâmetros do sistema, cadastro de equipamentos (IPs de impressoras) e reset de dados.
* **Gestor (`grupo_gestores`):** Acesso a dashboards de conferência e à tela de aprovação de armazenamento (exige inserção de senha para confirmar as guardas feitas no estoque).
* **Colaborador (`grupo_operadores`):** Acesso exclusivo às telas simplificadas de operação no coletor: bipagem de entrada, consulta ao mapa de put-away e separação de romaneios.

---

## 4. Fluxos Operacionais & Regras de Negócio `[INVARIANTE]`

### 4.1 Entrada (Recebimento e Conferência)
1. O operador abre a tela de recebimento no coletor. O cursor já inicia focado no primeiro campo (`autofocus`).
2. O operador seleciona o Produto (SKU) e digita manualmente o Sequencial e o Lote (transcrito da etiqueta/papelada prévia da produção). O peso é fixo em 500 kg, aplicado automaticamente pelo sistema — não há digitação de peso pelo operador.
3. O Django registra o palete e imediatamente monta o layout da etiqueta em ZPL e despacha os bytes via socket de rede para a impressora Zebra ZT421 (porta 9100), sem etapa de validação ou aprovação prévia — o objetivo do fluxo é nunca gerar paletes divergentes.

### 4.2 Armazenagem (Mapa e Put-away)
1. `[INVARIANTE]` O operador de empilhadeira só visualizará as orientações do mapa de armazenamento na tela se a transferência de saldo da produção para a logística já tiver sido processada no SAP B1.
2. Ao alocar o palete na estrutura física, o operador seleciona o endereço diretamente no mapa de estoque (grid clicável), sem digitar o código (ex: `001-02-A-20-01`).
3. `[INVARIANTE]` O Django ORM deve validar atômicamente se a ocupação do endereço selecionado é menor que 21. Se o endereço já possuir 21 paletes, a operação é rejeitada em tela e o operador é instruído a buscar outra posição.
4. Ao final da tarefa, o Gestor confere os paletes armazenados e digita sua senha de acesso para autorizar. A confirmação grava o estado e aciona a sincronização definitiva de localização no ERP.

---

## 5. Cronograma de Desenvolvimento (Milestones)

### Milestone 0: Fundação do Projeto
* Inicialização do projeto Django e configuração do `DATABASES` apontando para o arquivo `sqlite3` local.
* Estruturação dos aplicativos (`apps/recebimento`, `apps/estoque`, `apps/core`).
* Criação do arquivo `CLAUDE.md` com as diretrizes do robô.
* **Critério de Aceite:** `python manage.py migrate` e `python manage.py runserver` executam sem erros.

### Milestone 1: Autenticação e Matriz de Grupos
* Configuração do fluxo de Login/Logout baseado nas classes nativas do Django.
* Criação de uma Data Migration para popular os grupos (`Administradores`, `Gestores`, `Operadores`).
* Desenvolvimento de templates de formulários responsivos e com alto contraste para as telas de login no coletor.
* **Critério de Aceite:** Usuário operador tenta acessar rota de relatório do gestor e recebe um erro `403 Forbidden`.

### Milestone 2: Carga Inicial de Endereços
* Criação de um comando personalizado do Django (`python manage.py gerar_estrutura_armazem`).
* O comando deve popular a tabela de endereços criando a matriz exata de 120 posições (Guardas 01 a 20 combinadas aos andares A, B e C e aos lados 01/02), todos iniciando com `capacidade=21` e `ocupacao_atual=0`.
* **Critério de Aceite:** Verificação via banco de dados confirmando a existência exata de 120 registros únicos seguindo o padrão `001-LL-A-GG-01`.

### Milestone 3: Módulo de Entrada & Comunicação Socket ZPL
* Criação das Views de recebimento e mapeamento do modelo de Palete no Django ORM.
* Implementação da rotina em Python utilizando a biblioteca padrão `socket` para enviar comandos de texto ZPL via protocolo RAW TCP/IP direto para a impressora.
* Integração de tags HTML5 `autofocus` nos campos de entrada de dados.
* **Critério de Aceite:** Submissão do formulário salva o palete no banco SQLite com o status de conferido e dispara a rotina de envio de rede sem travar a interface do usuário.

### Milestone 4: Put-away e Controle de Concorrência
* Desenvolvimento da lógica de movimentação de paletes para os endereços cadastrados.
* `[INVARIANTE]` Implementação do método `.select_for_update()` do Django ORM no bloco de validação de guarda para blindar o sistema contra condições de corrida (múltiplos operadores tentando encher o mesmo endereço simultaneamente).
* Criação da interface visual de validação para o Gestor com campo de reautenticação por senha.
* **Critério de Aceite:** Um teste automatizado simulando requisições paralelas falha ao tentar inserir o 22º palete no mesmo endereço.

---

## 6. Estrutura de Modelos Sugerida (`models.py`)

```python
from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator

class Endereco(models.Model):
    guarda = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(20)])
    lado = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(2)])
    andar = models.CharField(max_length=1, choices=[('A', 'Andar A'), ('B', 'Andar B'), ('C', 'Andar C')])
    codigo = models.CharField(max_length=14, unique=True) # Ex: "001-01-A-04-01" — "001"/"01" fixos
    capacidade = models.IntegerField(default=21)
    ocupacao_atual = models.IntegerField(default=0)
    ativo = models.BooleanField(default=True)

    def __str__(self):
        return self.codigo

class Palete(models.Model):
    STATUS_CHOICES = [
        ('CRIADO', 'Criado na Produção'),
        ('CONFERIDO', 'Conferido pela Logística'),
        ('ARMAZENADO', 'Armazenado no Endereço'),
        ('FINALIZADO', 'Movimentação Sincronizada no SAP'),
    ]
    barcode_palete = models.CharField(max_length=50, unique=True)
    sku = models.CharField(max_length=40)
    descricao = models.CharField(max_length=200)
    lote = models.CharField(max_length=40)
    peso_kg = models.DecimalField(max_digits=12, decimal_places=3)
    estado = models.CharField(max_length=25, choices=STATUS_CHOICES, default='CRIADO')
    endereco = models.ForeignKey(Endereco, on_delete=models.SET_NULL, null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Palete {self.barcode_palete} - Lote {self.lote}"