from django.conf import settings
from django.db import models
from django.utils import timezone


class Romaneio(models.Model):
    STATUS_CHOICES = [
        ('ABERTO', 'Aguardando coleta'),
        ('COLETADO', 'Coletado'),
    ]

    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='romaneios')
    data = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='ABERTO')
    # Conferência cega: o romaneio não guarda cliente, código do cliente nem datas da
    # nota — só as notas fiscais de origem (RomaneioNota) e os itens a separar.
    criado_em = models.DateTimeField(auto_now_add=True)
    # Confirmação da coleta pelo operador da Logística (depois de bipar todos os itens).
    coleta_confirmada_em = models.DateTimeField(null=True, blank=True)
    coleta_confirmada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )

    class Meta:
        ordering = ['-criado_em', '-pk']

    def __str__(self):
        return f'Romaneio {self.pk}'

    @property
    def peso_total(self):
        return sum(item.peso_kg for item in self.itens.all())


class RomaneioNota(models.Model):
    """Nota Fiscal de Saída do SAP que originou parte do romaneio. Um romaneio pode
    reunir várias notas; o unique garante que cada nota entre em um só romaneio."""

    romaneio = models.ForeignKey(Romaneio, on_delete=models.CASCADE, related_name='notas')
    docentry = models.PositiveIntegerField(unique=True)
    docnum = models.PositiveIntegerField('nota fiscal')

    class Meta:
        ordering = ['docnum']

    def __str__(self):
        return f'Nota {self.docnum}'


class RomaneioItem(models.Model):
    """Uma parcela retirada de um palete que sai neste romaneio. Produto e peso
    são copiados da Retirada; o OneToOne impede a mesma retirada em dois romaneios."""

    romaneio = models.ForeignKey(Romaneio, on_delete=models.CASCADE, related_name='itens')
    retirada = models.OneToOneField('producao.Retirada', on_delete=models.PROTECT, related_name='item_romaneio')
    sku = models.CharField(max_length=40)
    descricao = models.CharField(max_length=200)
    peso_kg = models.DecimalField(max_digits=12, decimal_places=3)
    # Nota, linha e preço na Nota Fiscal de Saída do SAP (nulos nos romaneios antigos).
    nota = models.ForeignKey(RomaneioNota, on_delete=models.CASCADE, null=True, blank=True, related_name='itens')
    nota_linha = models.PositiveIntegerField(null=True, blank=True)
    preco_unitario = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    # Posição de onde o palete sai, copiada na criação (um palete esvaziado perde o endereço).
    corredor = models.CharField(max_length=1, blank=True, default='')
    andar = models.PositiveSmallIntegerField(null=True, blank=True)
    posicao = models.PositiveSmallIntegerField(null=True, blank=True)
    coletado_em = models.DateTimeField(null=True, blank=True)
    coletado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
    )

    class Meta:
        ordering = ['pk']

    def __str__(self):
        return f'{self.sku} — {self.peso_kg} kg'

    @property
    def valor(self):
        return None if self.preco_unitario is None else self.preco_unitario * self.peso_kg


class Entrega(models.Model):
    """Confirmação de entrega de um romaneio. A assinatura digital é o nome de
    usuário de quem clicou em "Assinar", gravado no momento. Os nomes do
    motorista e do ajudante também são copiados, para o registro continuar fiel
    se o cadastro mudar."""

    romaneio = models.OneToOneField(Romaneio, on_delete=models.CASCADE, related_name='entrega')
    registrada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    motorista_nome = models.CharField(max_length=120)
    ajudante_nome = models.CharField(max_length=120)
    assinatura_usuario = models.CharField(max_length=150, default='')
    assinado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Entrega do romaneio {self.romaneio_id}'
