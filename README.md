# WMS — Controle de Estoque por Lote

Sistema de controle de estoque por lote, atuando como camada intermediária entre Produção e Logística. Não substitui o ERP, que segue como fonte única de saldo.

## Funcionalidades

- Emissão de etiquetas de palete com lote, peso e data de fabricação
- Transferência Produção → Logística com bipagem e aprovação em lote
- Reprovação, correção e reimpressão de etiqueta
- Put-away com alocação automática de posições
- Mapa de estoque 3D por andar e corredor, com consulta de palete, lote e corredor (inclusive por leitura de QR Code pela câmera)
- Etiquetas de identificação de corredor
- Romaneio: montagem a partir de notas fiscais, coleta por bipagem dos paletes e confirmação de entrega com assinatura digital
- Consulta somente leitura ao ERP (ordens de produção e estoque disponível) nas estatísticas de produção
- Histórico completo de cada palete, relatórios e estatísticas
- Autenticação corporativa com perfis de acesso

## Stack

| Componente | Tecnologia |
|-----------|-----------|
| Backend | Python / Django |
| Banco | SQLite (dev) / MySQL (produção) |
| Frontend | HTML5 + Tailwind CSS |
| Impressão | Etiquetas ZPL (Zebra) |
| Coleta | Coletores Zebra (DataWedge) e leitura de QR Code pelo navegador |

## Fluxo do palete

```
FABRICADO → TRANSFERENCIA_PRODUCAO → APROVADO_PRODUCAO → TRANSFERENCIA_LOGISTICA → APROVADO_LOGISTICA → ARMAZENADO → RETIRADO
```

Em qualquer etapa anterior ao armazenamento, o palete pode ser reprovado, corrigido e reenviado ao fluxo.
## Licença

Família do Sítio &copy 2026. Todos os direitos reservados.
