import re
from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.logistica.models import Produto

from .models import UNIDADE_CHOICES, UNIDADES_CONTAVEIS, Palete

ESTADOS_LOTE_EM_PRODUCAO = ('FABRICADO', 'TRANSFERENCIA_PRODUCAO', 'REPROVADO')


# Lote vai para a etiqueta ZPL e para a querystring: só caracteres seguros.
LOTE_VALIDO = re.compile(r'^[A-Z0-9][A-Z0-9._/-]*$')

_CLASSE_CAMPO = 'input-field input-field-lg'


def _validar_vencimento(form, cleaned_data):
    fabricacao = cleaned_data.get('data_fabricacao')
    vencimento = cleaned_data.get('data_vencimento')
    if fabricacao and vencimento and vencimento < fabricacao:
        form.add_error('data_vencimento', 'O vencimento não pode ser anterior à data de fabricação.')


class RecebimentoForm(forms.Form):
    produto = forms.ModelChoiceField(
        queryset=Produto.objects.filter(ativo=True),
        label='Produto',
        widget=forms.Select(attrs={'autofocus': True, 'class': _CLASSE_CAMPO}),
    )
    unidade = forms.ChoiceField(
        label='Unidade de medida',
        choices=UNIDADE_CHOICES,
        initial='kg',
        required=False,  # ausente (formulário antigo) = kg
        widget=forms.Select(attrs={'class': _CLASSE_CAMPO}),
    )
    # Só em un/cx: em kg o palete usa o peso fixo (PESO_FIXO_RECEBIMENTO_KG).
    quantidade_palete = forms.IntegerField(
        label='Quantidade por palete',
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={'autocomplete': 'off', 'class': _CLASSE_CAMPO, 'min': '1', 'step': '1'}),
    )
    lote = forms.CharField(
        label='Lote',
        max_length=60,
        widget=forms.TextInput(attrs={'autocomplete': 'off', 'class': _CLASSE_CAMPO}),
    )
    quantidade = forms.IntegerField(
        label='Quantidade de Paletes',
        min_value=1,
        max_value=50,
        initial=1,
        widget=forms.NumberInput(attrs={'autocomplete': 'off', 'class': _CLASSE_CAMPO}),
    )
    data_fabricacao = forms.DateField(
        label='Data de Fabricação',
        widget=forms.DateInput(attrs={'type': 'date', 'class': _CLASSE_CAMPO}, format='%Y-%m-%d'),
    )
    data_vencimento = forms.DateField(
        label='Data de Vencimento',
        widget=forms.DateInput(attrs={'type': 'date', 'class': _CLASSE_CAMPO}, format='%Y-%m-%d'),
    )
    peso_variavel = forms.BooleanField(
        label='Peso variável',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'h-6 w-6'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['data_fabricacao'].widget.attrs['max'] = timezone.localdate().isoformat()

    def clean_data_fabricacao(self):
        data = self.cleaned_data['data_fabricacao']
        if data > timezone.localdate():
            raise forms.ValidationError('A data de fabricação não pode ser futura.')
        return data

    def clean_unidade(self):
        return self.cleaned_data.get('unidade') or 'kg'

    def clean_lote(self):
        lote = self.cleaned_data['lote'].strip().upper()
        if not LOTE_VALIDO.match(lote):
            raise forms.ValidationError('Use apenas letras (sem acento), números e os símbolos . _ / -')
        ja_aprovado = Palete.objects.filter(lote=lote).exclude(estado__in=ESTADOS_LOTE_EM_PRODUCAO).exists()
        if ja_aprovado:
            raise forms.ValidationError(
                f'O lote "{lote}" já foi aprovado e não pode receber novos paletes.'
            )
        return lote

    def clean(self):
        cleaned_data = super().clean()
        contavel = cleaned_data.get('unidade') in UNIDADES_CONTAVEIS
        if contavel and not cleaned_data.get('quantidade_palete') and 'quantidade_palete' not in self.errors:
            self.add_error('quantidade_palete', 'Informe a quantidade por palete.')
        if not contavel:
            cleaned_data['quantidade_palete'] = None
        if cleaned_data.get('peso_variavel') and cleaned_data.get('quantidade'):
            self._validar_peso_ultimo(cleaned_data, contavel)
        _validar_vencimento(self, cleaned_data)
        return cleaned_data

    def _validar_peso_ultimo(self, cleaned_data, contavel):
        """Peso variável: só o último palete tem peso (ou, em un/cx, quantidade) próprio
        (campo `peso_ultimo`); os demais usam o fixo / a quantidade por palete."""
        if contavel:
            campo = forms.IntegerField(min_value=1)
            erro = 'Informe uma quantidade inteira (maior que zero) para o último palete.'
        else:
            campo = forms.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal('0.001'))
            erro = 'Informe um peso válido (maior que zero) para o último palete.'
        try:
            cleaned_data['peso_ultimo'] = Decimal(campo.clean(self.data.get('peso_ultimo', '')))
        except forms.ValidationError:
            self.add_error(None, erro)


class CorrecaoPaleteForm(forms.Form):
    """Correção, pelo Gestor, de um palete reprovado (item e peso; a data é opcional)."""

    produto = forms.ModelChoiceField(
        queryset=Produto.objects.filter(ativo=True),
        label='Item',
        widget=forms.Select(attrs={'class': 'input-field'}),
    )
    peso_kg = forms.DecimalField(
        label='Quantidade',
        max_digits=12,
        decimal_places=3,
        min_value=Decimal('0.001'),
        widget=forms.NumberInput(attrs={'step': '0.001', 'class': 'input-field'}),
    )
    data_fabricacao = forms.DateField(
        label='Data de Fabricação',
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'input-field'}, format='%Y-%m-%d'),
    )
    data_vencimento = forms.DateField(
        label='Data de Vencimento',
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'input-field'}, format='%Y-%m-%d'),
    )

    def clean_data_fabricacao(self):
        data = self.cleaned_data['data_fabricacao']
        if data and data > timezone.localdate():
            raise forms.ValidationError('A data de fabricação não pode ser futura.')
        return data

    def clean(self):
        cleaned_data = super().clean()
        _validar_vencimento(self, cleaned_data)
        return cleaned_data
