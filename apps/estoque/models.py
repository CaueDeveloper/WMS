from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Endereco(models.Model):
    ANDAR_CHOICES = [
        ('A', 'Andar A'),
        ('B', 'Andar B'),
        ('C', 'Andar C'),
    ]

    corredor = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(20)])
    andar = models.CharField(max_length=1, choices=ANDAR_CHOICES)
    codigo = models.CharField(max_length=10, unique=True)  # Ex: "02-B"
    capacidade = models.IntegerField(default=21)
    ocupacao_atual = models.IntegerField(default=0)
    ativo = models.BooleanField(default=True)

    def __str__(self):
        return self.codigo


class AreaDestino(models.Model):
    """Área física de transição para onde o Gestor envia o palete aprovado, antes do put-away."""

    nome = models.CharField(max_length=100, unique=True)
    ativo = models.BooleanField(default=True)

    def __str__(self):
        return self.nome


class Produto(models.Model):
    """Catálogo de itens selecionáveis no recebimento (ex.: "Café 500g"). SKU é fixo por item."""

    sku = models.CharField(max_length=40, unique=True)
    descricao = models.CharField(max_length=200)
    ativo = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.sku} — {self.descricao}'
