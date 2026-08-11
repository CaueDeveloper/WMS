import uuid

from django.db import models


def gerar_barcode_palete():
    return f'PLT-{uuid.uuid4().hex[:12].upper()}'


class Palete(models.Model):
    STATUS_CHOICES = [
        ('CRIADO', 'Etiqueta Impressa — Aguardando Bipagem de Transferência'),
        ('EM_TRANSFERENCIA', 'Bipado para Transferência — Aguardando Aprovação de Lote'),
        ('CONFERIDO', 'Conferido e Aprovado pelo Gestor'),
        ('ARMAZENADO', 'Armazenado no Endereço'),
        ('FINALIZADO', 'Movimentação Sincronizada no SAP'),
        ('RETIRADO', 'Retirado do Mapa — Saída Física do Estoque'),
    ]

    barcode_palete = models.CharField(max_length=50, unique=True, default=gerar_barcode_palete)
    sku = models.CharField(max_length=40)
    descricao = models.CharField(max_length=200)
    lote = models.CharField(max_length=60)
    peso_kg = models.DecimalField(max_digits=12, decimal_places=3)
    estado = models.CharField(max_length=25, choices=STATUS_CHOICES, default='CRIADO')
    area_destino = models.ForeignKey('estoque.AreaDestino', on_delete=models.SET_NULL, null=True, blank=True)
    endereco = models.ForeignKey('estoque.Endereco', on_delete=models.SET_NULL, null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['estado', 'lote'], name='palete_estado_lote_idx'),
        ]

    def __str__(self):
        return f'Palete {self.barcode_palete} - Lote {self.lote}'
