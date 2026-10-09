from django.contrib import admin

from .models import AreaDestino, Endereco, Produto


@admin.register(Endereco)
class EnderecoAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'corredor', 'andar', 'posicao', 'capacidade', 'ativo')
    list_filter = ('corredor', 'andar', 'ativo')
    search_fields = ('codigo',)
    ordering = ('corredor', 'andar', 'posicao')

    def get_readonly_fields(self, request, obj=None):
        # O código é derivado de corredor/andar/posição: editar um sem o outro os desacopla.
        return ('codigo', 'corredor', 'andar', 'posicao') if obj else ()


@admin.register(AreaDestino)
class AreaDestinoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ativo')
    list_filter = ('ativo',)


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = ('sku', 'descricao', 'ativo', 'peso_variavel')
    list_filter = ('ativo', 'peso_variavel')
    search_fields = ('sku', 'descricao')
