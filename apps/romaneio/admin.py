from django.contrib import admin

from .models import Romaneio, RomaneioItem, RomaneioNota


class SomenteSuperuserEdita:
    """Os demais usuários do admin só consultam. Editar ou apagar aqui NÃO refaz a
    retirada nem o saldo dos paletes (isso só acontece em `criar_romaneio`)."""

    def has_add_permission(self, request, *args):
        return request.user.is_superuser

    def has_change_permission(self, request, *args):
        return request.user.is_superuser

    def has_delete_permission(self, request, *args):
        return request.user.is_superuser


class RomaneioNotaInline(SomenteSuperuserEdita, admin.TabularInline):
    model = RomaneioNota
    extra = 0


class RomaneioItemInline(SomenteSuperuserEdita, admin.TabularInline):
    model = RomaneioItem
    extra = 0
    raw_id_fields = ('retirada', 'nota')


@admin.register(Romaneio)
class RomaneioAdmin(SomenteSuperuserEdita, admin.ModelAdmin):
    list_display = ('id', 'data', 'criado_por', 'status')
    list_filter = ('status', 'data')
    search_fields = ('itens__sku', 'itens__descricao')
    date_hierarchy = 'data'
    raw_id_fields = ('criado_por',)
    inlines = [RomaneioNotaInline, RomaneioItemInline]
