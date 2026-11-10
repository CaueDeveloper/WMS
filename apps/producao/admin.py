from django import forms
from django.contrib import admin

from .models import Palete, PaleteHistorico, registrar_historico


class SomenteLeituraAdmin(admin.ModelAdmin):
    """Estado, endereço e histórico só mudam pelo fluxo da aplicação (que valida
    a máquina de estados e grava o histórico); o admin serve apenas para consulta."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Só o superusuário exclui. Excluir um Palete apaga também o histórico (CASCADE),
        # e o admin exige permissão de exclusão nos dois models.
        return request.user.is_superuser


class PaleteCriacaoForm(forms.ModelForm):
    class Meta:
        model = Palete
        fields = (
            'barcode_palete', 'sku', 'descricao', 'lote', 'peso_kg', 'data_fabricacao', 'data_vencimento',
            'estado', 'area_destino', 'endereco',
        )

    def clean(self):
        dados = super().clean()
        estado, endereco = dados.get('estado'), dados.get('endereco')
        if estado == 'ARMAZENADO' and endereco is None:
            self.add_error('endereco', 'Palete ARMAZENADO precisa de um endereço.')
        elif estado and estado != 'ARMAZENADO' and endereco is not None:
            self.add_error('endereco', 'Só palete ARMAZENADO ocupa endereço.')
        return dados


@admin.register(Palete)
class PaleteAdmin(SomenteLeituraAdmin):
    list_display = ('barcode_palete', 'sku', 'lote', 'peso_kg', 'estado', 'endereco', 'criado_em')
    list_filter = ('estado',)
    search_fields = ('barcode_palete', 'sku', 'lote')

    def has_add_permission(self, request):
        # Só o superusuário cria e edita paletes por aqui. Ao editar, ele passa por cima da
        # máquina de estados do fluxo normal (a validação de estado x endereço continua).
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_form(self, request, obj=None, **kwargs):
        kwargs['form'] = PaleteCriacaoForm
        return super().get_form(request, obj, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        # O código já está impresso na etiqueta do palete: não muda depois de criado.
        return ('barcode_palete',) if obj else ()

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change or 'estado' in form.changed_data:
            # Fica no histórico quem criou/alterou e em que estado, como no fluxo normal.
            registrar_historico([obj], obj.estado, usuario=request.user)


@admin.register(PaleteHistorico)
class PaleteHistoricoAdmin(SomenteLeituraAdmin):
    list_display = ('palete', 'estado', 'usuario', 'criado_em')
    list_filter = ('estado',)
    search_fields = ('palete__barcode_palete',)
