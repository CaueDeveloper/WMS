import uuid

from django.db import models, transaction


def gerar_barcode_palete():
    return f'PLT-{uuid.uuid4().hex[:12].upper()}'


class ContadorLote(models.Model):
    """Linha única cujo valor gera os códigos de lote sequenciais (LT00001, LT00002, ...)."""

    proximo_numero = models.PositiveIntegerField(default=1)


def gerar_proximo_lote():
    with transaction.atomic():
        contador, _ = ContadorLote.objects.select_for_update().get_or_create(pk=1)
        numero = contador.proximo_numero
        contador.proximo_numero = numero + 1
        contador.save(update_fields=['proximo_numero'])
    return f'LT{numero:05d}'


class Palete(models.Model):
    STATUS_CHOICES = [
        ('CRIADO', 'Aguardando Conferência do Gestor'),
        ('CONFERIDO', 'Conferido e Aprovado pelo Gestor'),
        ('DIVERGENTE', 'Divergência de Peso'),
        ('ARMAZENADO', 'Armazenado no Endereço'),
        ('FINALIZADO', 'Movimentação Sincronizada no SAP'),
    ]

    barcode_palete = models.CharField(max_length=50, unique=True, default=gerar_barcode_palete)
    sku = models.CharField(max_length=40)
    descricao = models.CharField(max_length=200)
    lote = models.CharField(max_length=40)
    peso_kg = models.DecimalField(max_digits=12, decimal_places=3)
    estado = models.CharField(max_length=25, choices=STATUS_CHOICES, default='CRIADO')
    area_destino = models.ForeignKey('estoque.AreaDestino', on_delete=models.SET_NULL, null=True, blank=True)
    endereco = models.ForeignKey('estoque.Endereco', on_delete=models.SET_NULL, null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Palete {self.barcode_palete} - Lote {self.lote}'
