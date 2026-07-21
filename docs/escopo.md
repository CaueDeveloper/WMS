# Escopo do Projeto: Sistema WMS (Café do Sítio)

## 1. Visão Geral do Projeto
O objetivo principal do Projeto WMS é implantar o controle de estoque por lote de ponta a ponta na transição entre os setores de Produção e Logística da Café do Sítio. O sistema atuará como uma camada intermediária de validação, registrando a criação de paletes, pesagens, endereçamento físico e separação de romaneios, garantindo rastreabilidade, integridade de dados e eliminação de processos manuais (como conferências via WhatsApp).

---

## 2. Escopo Técnico (Stack Tecnológica)
* **Backend:** Python 3.11+ / Django Framework
* **Banco de Dados:** SQLite3 (Arquivo local para desenvolvimento ágil) / MySQL (Ambiente de Produção) via Django ORM
* **Frontend:** HTML5, CSS3 (Design responsivo e simplificado, otimizado para a tela de 4" 800×480 do coletor Zebra MC2700)
* **Comunicação de Hardware:** Biblioteca padrão `socket` do Python para envio de comandos brutos (RAW) via TCP/IP na porta 9100 para a impressora térmica Zebra ZT421.
* **Captura de Dados:** Integração nativa com o Zebra DataWedge configurado em modo emulação de teclado (Keystroke).

---

## 3. Arquitetura de Dados (Modelos no Django)
A estrutura de tabelas será gerenciada integralmente pelo Django ORM, garantindo portabilidade entre SQLite3 e MySQL:

* **Endereços (`Endereco`):** Matriz de armazenamento físico do galpão representando Corredores (01 a 20) e Andares (A, B, C). Controla a capacidade máxima rígida de 21 paletes por endereço.
* **Paletes (`Palete`):** Unidade logística principal. Registra o código de barras gerado, SKU, lote, peso real coletado, localização atual e status do ciclo de vida.
* **Histórico de Movimentações:** Logs de auditoria vinculados ao sistema de usuários do Django (`auth.User`) para rastrear quem, quando e para onde moveu cada palete.
* **Fila de Integração (Outbox):** Tabela intermediária para registrar eventos de escrita e sincronização pendentes com o SAP B1.

---

## 4. Fluxo Operacional & Regras de Negócio

### 4.1. Recebimento e Conferência (Entrada)
1. A produção agrupa as embalagens de café em paletes, filma, pesa e gera uma etiqueta inicial com data e peso.
2. Na área de transição, a Logística utiliza o coletor Zebra na tela do sistema WMS.
3. O operador lê o SKU, o Lote e digita/confirma o peso de cada palete. Os campos utilizam a tag HTML5 `autofocus` para permitir bipagem contínua sem cliques do mouse.
4. O sistema valida os dados e o backend Django dispara diretamente via socket de rede a impressão da etiqueta ZPL interna para identificação definitiva do palete.
5. `[INVARIANTE]` Se houver divergências nos dados cadastrais ou falha de validação, o palete é marcado como `DIVERGENTE` e travado no fluxo.

### 4.2. Mapa de Armazenamento e Guardada (Estoque)
1. `[INVARIANTE]` O operador de empilhadeira só visualiza o mapa de armazenamento de um lote se a transferência física de saldo da Produção (`001-06`) para a Logística (`001-02`) já tiver sido efetivada no SAP B1.
2. Ao colocar o palete na prateleira física, o operador lê a etiqueta do palete e o código de barras do endereço (ex: `14-C`).
3. `[INVARIANTE]` O Django ORM utiliza a trava de concorrência `.select_for_update()` para validar de forma isolada se a ocupação do endereço é menor que 21. Se o endereço estiver cheio, o bipe é rejeitado imediatamente na tela do coletor.
4. Ao final da tarefa, o Gestor de Logística revisa o comparativo entre o planejado e o executado, digitando sua senha de acesso para autorizar o encerramento do lote de guarda e disparar a atualização no ERP.

### 4.3. Saída e Separação (Expedição)
1. O WMS consome os dados do Romaneio de Carga gerados originalmente no SAP B1.
2. O sistema enriquece o romaneio indicando na tela do separador exatamente de qual **endereço** e de qual **lote** o produto deve ser retirado (aplicando lógica FIFO).
3. O operador executa as baixas bipando as etiquetas à medida que carrega o veículo.

---

## 5. Matriz de Permissões (Django Groups)
* **ADMIN:** Acesso total ao painel administrativo do Django (`/admin`), parametrização de IPs de rede das impressoras e gerenciamento de usuários.
* **Gestor:** Acesso a relatórios de divergência de peso, dashboards operacionais e à view de liberação/aprovação por senha.
* **Colaborador:** Interface minimalista e de alto contraste adaptada para o coletor (bipes de entrada, conferência de mapa e separação).

---

## 6. Cronograma de Entregas (Milestones)

* **Milestone 0: Fundação do Projeto**
  * Configuração inicial do Django com banco SQLite3 e mapeamento de diretórios de apps.
  * Criação do arquivo `CLAUDE.md`.
* **Milestone 1: Autenticação e Matriz de Grupos**
  * Configuração do fluxo de login e criação automática dos grupos de permissões via Data Migration.
* **Milestone 2: Carga Inicial de Endereços**
  * Desenvolvimento do comando customizado `python manage.py gerar_estrutura_armazem` para criar a matriz exata de 60 posições (`CC-A`).
* **Milestone 3: Módulo de Entrada & Impressão Socket**
  * Criação das Views de recebimento com foco automático e rotina em Python para envio da string ZPL via TCP/IP na porta 9100.
* **Milestone 4: Put-away e Proteção de Capacidade**
  * Lógica de guarda de paletes bloqueando transações concorrentes acima do limite de 21 itens por endereço através de travas no banco de dados.