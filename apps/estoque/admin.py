from django.contrib import admin

from .models import AreaDestino, Endereco, Produto


@admin.register(Endereco)
class EnderecoAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'lado', 'modulo', 'andar', 'capacidade', 'ocupacao_atual', 'ativo')
    list_filter = ('lado', 'andar', 'ativo')
    ordering = ('lado', 'modulo', 'andar')


@admin.register(AreaDestino)
class AreaDestinoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ativo')
    list_filter = ('ativo',)


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = ('sku', 'descricao', 'ativo', 'peso_variavel')
    list_filter = ('ativo', 'peso_variavel')
    search_fields = ('sku', 'descricao')
