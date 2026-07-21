from django.contrib import admin

from .models import AreaDestino, Endereco, Produto


@admin.register(Endereco)
class EnderecoAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'corredor', 'andar', 'capacidade', 'ocupacao_atual', 'ativo')
    list_filter = ('andar', 'ativo')
    ordering = ('corredor', 'andar')


@admin.register(AreaDestino)
class AreaDestinoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ativo')
    list_filter = ('ativo',)


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = ('sku', 'descricao', 'ativo')
    list_filter = ('ativo',)
    search_fields = ('sku', 'descricao')
