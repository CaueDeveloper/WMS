import uuid

from django.conf import settings
from django.db import models


def gerar_barcode_palete():
    return f'PLT-{uuid.uuid4().hex[:12].upper()}'


class Palete(models.Model):
    STATUS_CHOICES = [
        ('CRIADO', 'Etiqueta Impressa — Aguardando Bipagem de Transferência'),
        ('EM_TRANSFERENCIA', 'Bipado para Transferência — Aguardando Aprovação de Lote'),
        ('CONFERIDO', 'Conferido e Aprovado pelo Gestor'),
        ('EM_CONFERENCIA_LOGISTICA', 'Bipado pela Logística — Aguardando Aprovação de Lote'),
        ('CONFERIDO_LOGISTICA', 'Conferido e Recebido pela Logística'),
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
    aprovado_producao_em = models.DateTimeField(null=True, blank=True)
    aprovado_logistica_em = models.DateTimeField(null=True, blank=True)
    armazenado_em = models.DateTimeField(null=True, blank=True)
    retirado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['estado', 'lote'], name='palete_estado_lote_idx'),
        ]

    def __str__(self):
        return f'Palete {self.barcode_palete} - Lote {self.lote}'


class PaleteHistorico(models.Model):
    """Uma entrada por transição de `estado` de um Palete — a série completa,
    em ordem, é o "fluxo desde a criação até a última etapa" exibido na tela
    de Inspecionar (apps/estoque/views.py::inspecionar_palete)."""

    palete = models.ForeignKey(Palete, on_delete=models.CASCADE, related_name='historico')
    estado = models.CharField(max_length=25, choices=Palete.STATUS_CHOICES)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['criado_em']

    def __str__(self):
        return f'{self.palete.barcode_palete} -> {self.estado}'


def registrar_historico(paletes, estado, usuario=None):
    """Grava uma entrada de PaleteHistorico para cada palete em `paletes`
    (lista/queryset já materializada) no estado `estado`. Usa bulk_create —
    não dispara signals, mas auto_now_add é aplicado normalmente em INSERT."""
    PaleteHistorico.objects.bulk_create([
        PaleteHistorico(palete=p, estado=estado, usuario=usuario) for p in paletes
    ])
