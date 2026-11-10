from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Count, Q


class EnderecoQuerySet(models.QuerySet):
    def com_ocupacao(self):
        """Anota `ocupacao` = paletes ARMAZENADO no endereço, contados direto
        da tabela de paletes (não há contador guardado no endereço)."""
        return self.annotate(ocupacao=Count('palete', filter=Q(palete__estado='ARMAZENADO')))


class Endereco(models.Model):
    """Uma posição de palete do armazém: corredor (A–T) × andar (1–3) ×
    posição (1–21), código no formato `001-02-A-01-01` (`001-02` fixo)."""

    CORREDORES = 'ABCDEFGHIJKLMNOPQRST'
    ANDARES = (1, 2, 3)
    POSICOES = range(1, 22)
    PREFIXO_CODIGO = '001-02'

    corredor = models.CharField(max_length=1, choices=[(c, c) for c in CORREDORES])
    andar = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(3)])
    posicao = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(21)])
    codigo = models.CharField(max_length=14, unique=True)  # Ex: "001-02-A-01-01"
    capacidade = models.IntegerField(default=1)
    ativo = models.BooleanField(default=True)

    objects = EnderecoQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['corredor', 'andar', 'posicao'], name='endereco_corredor_andar_posicao_uniq'),
        ]

    @staticmethod
    def montar_codigo(corredor, andar, posicao):
        return f'{Endereco.PREFIXO_CODIGO}-{corredor}-{andar:02d}-{posicao:02d}'

    @staticmethod
    def montar_codigo_corredor(corredor):
        return f'{Endereco.PREFIXO_CODIGO}-{corredor}'

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
    peso_variavel = models.BooleanField(
        default=False,
        help_text='Quando marcado, o peso deste item não é fixo em '
        'PESO_FIXO_RECEBIMENTO_KG — o colaborador digita o peso real no recebimento.',
    )

    def __str__(self):
        return f'{self.sku} — {self.descricao}'
