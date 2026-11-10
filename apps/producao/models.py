import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def gerar_barcode_palete():
    return f'PLT-{uuid.uuid4().hex[:12].upper()}'


class Palete(models.Model):
    STATUS_CHOICES = [
        ('FABRICADO', 'Fabricado na Produção'),
        ('TRANSFERENCIA_PRODUCAO', 'Conferido pela Produção'),
        ('APROVADO_PRODUCAO', 'Aprovado pela Produção'),
        ('TRANSFERENCIA_LOGISTICA', 'Conferido pela Logística'),
        ('APROVADO_LOGISTICA', 'Aprovado pela Logística'),
        ('ARMAZENADO', 'Armazenado no Estoque'),
        ('RETIRADO', 'Palete retirado do Estoque'),
        ('REPROVADO', 'Palete reprovado'),
    ]

    barcode_palete = models.CharField(max_length=50, unique=True, default=gerar_barcode_palete)
    sku = models.CharField(max_length=40)
    descricao = models.CharField(max_length=200)
    lote = models.CharField(max_length=60)
    peso_kg = models.DecimalField(max_digits=12, decimal_places=3)
    # Nulo apenas para paletes anteriores à introdução do campo.
    data_fabricacao = models.DateField(null=True, blank=True)
    # Informada pela Produção na emissão; nulo para paletes anteriores ao campo.
    data_vencimento = models.DateField(null=True, blank=True)
    estado = models.CharField(max_length=25, choices=STATUS_CHOICES, default='FABRICADO')
    area_destino = models.ForeignKey('logistica.AreaDestino', on_delete=models.SET_NULL, null=True, blank=True)
    endereco = models.ForeignKey('logistica.Endereco', on_delete=models.PROTECT, null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    aprovado_producao_em = models.DateTimeField(null=True, blank=True)
    aprovado_logistica_em = models.DateTimeField(null=True, blank=True)
    armazenado_em = models.DateTimeField(null=True, blank=True)
    retirado_em = models.DateTimeField(null=True, blank=True)
    reprovado_em = models.DateTimeField(null=True, blank=True)
    motivo_reprovacao = models.TextField(blank=True, default='')
    # Etapa em que foi reprovado ('PRODUCAO' ou 'LOGISTICA'), só informativo.
    reprovado_na_etapa = models.CharField(max_length=10, blank=True, default='')

    class Meta:
        indexes = [
            models.Index(fields=['estado', 'lote'], name='palete_estado_lote_idx'),
        ]
        constraints = [
            # Capacidade 1 por posição, garantida no banco (ignorada no MySQL,
            # que não suporta índice único condicional — lá vale o lock em alocar_lote).
            models.UniqueConstraint(
                fields=['endereco'], condition=models.Q(estado='ARMAZENADO'),
                name='palete_um_armazenado_por_endereco',
            ),
        ]

    def __str__(self):
        return f'Palete {self.barcode_palete} - Lote {self.lote}'

    @property
    def data_fabricacao_exibida(self):
        """Data de fabricação; paletes antigos (sem o campo) caem na data de criação."""
        return self.data_fabricacao or timezone.localtime(self.criado_em).date()


class PaleteHistorico(models.Model):
    """Uma entrada por transição de `estado` de um Palete — a série completa,
    em ordem, é o "fluxo desde a criação até a última etapa" exibido na tela
    de Inspecionar (apps/logistica/views.py::inspecionar_palete)."""

    palete = models.ForeignKey(Palete, on_delete=models.CASCADE, related_name='historico')
    estado = models.CharField(max_length=25, choices=Palete.STATUS_CHOICES)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['criado_em']

    def __str__(self):
        return f'{self.palete.barcode_palete} -> {self.estado}'


class Retirada(models.Model):
    """Parcela retirada de um palete ARMAZENADO. `Palete.peso_kg` guarda o saldo
    atual; cada retirada registra quanto saiu, o saldo restante e quem
    modificou ("Modificado por" na consulta do palete)."""

    palete = models.ForeignKey(Palete, on_delete=models.CASCADE, related_name='retiradas')
    peso_kg = models.DecimalField(max_digits=12, decimal_places=3)
    saldo_kg = models.DecimalField(max_digits=12, decimal_places=3)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['criado_em', 'pk']

    def __str__(self):
        return f'{self.palete.barcode_palete}: -{self.peso_kg} kg'


def registrar_historico(paletes, estado, usuario=None):
    """Grava uma entrada de PaleteHistorico para cada palete em `paletes`
    (lista/queryset já materializada) no estado `estado`. Usa bulk_create —
    não dispara signals, mas auto_now_add é aplicado normalmente em INSERT."""
    PaleteHistorico.objects.bulk_create([
        PaleteHistorico(palete=p, estado=estado, usuario=usuario) for p in paletes
    ])


class OrdemProducao(models.Model):
    """OP importada do SAP (somente leitura no WMS)."""
    doc_entry = models.PositiveIntegerField(unique=True)
    doc_num = models.PositiveIntegerField()
    sku = models.CharField(max_length=50, db_index=True)
    descricao = models.CharField(max_length=200, blank=True)
    quantidade_planejada = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantidade_produzida = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    status = models.CharField(max_length=30, blank=True)
    data_lancamento = models.DateField(db_index=True)
    sincronizado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-data_lancamento', '-doc_num']

    def __str__(self):
        return f'OP {self.doc_num} — {self.sku}'


class EstoqueItemSAP(models.Model):
    """Saldo disponível do item no SAP, copiado na sincronização."""
    sku = models.CharField(max_length=50, unique=True)
    disponivel = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    sincronizado_em = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f'{self.sku}: {self.disponivel}'
