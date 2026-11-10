# WMS — Controle de Estoque por Lote

Sistema de controle de estoque por lote, atuando como camada intermediária entre Produção e Logística. Não substitui o ERP, que segue como fonte única de saldo.

## Funcionalidades

- Emissão de etiquetas de palete com lote, peso e data de fabricação
- Transferência Produção → Logística com bipagem e aprovação em lote
- Reprovação, correção e reimpressão de etiqueta
- Put-away com alocação automática de posições
- Mapa de estoque, consulta de palete/lote e retirada
- Histórico completo de cada palete, relatórios e estatísticas
- Autenticação corporativa com perfis de acesso

## Stack

| Componente | Tecnologia |
|-----------|-----------|
| Backend | Python / Django |
| Banco | SQLite (dev) / MySQL (produção) |
| Frontend | HTML5 + Tailwind CSS |
| Impressão | Etiquetas ZPL (Zebra) |
| Coleta | Coletores Zebra (DataWedge) |

## Fluxo do palete

```
FABRICADO → TRANSFERENCIA_PRODUCAO → APROVADO_PRODUCAO → TRANSFERENCIA_LOGISTICA → APROVADO_LOGISTICA → ARMAZENADO → RETIRADO
```

Em qualquer etapa anterior ao armazenamento, o palete pode ser reprovado, corrigido e reenviado ao fluxo.

## Licença

Projeto proprietário. Todos os direitos reservados.
