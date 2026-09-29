from decimal import Decimal

from django import forms

from apps.estoque.models import Produto

from .models import Palete

ESTADOS_LOTE_EM_PRODUCAO = ('CRIADO', 'EM_TRANSFERENCIA')


_CLASSE_CAMPO = 'input-field input-field-lg'


class RecebimentoForm(forms.Form):
    produto = forms.ModelChoiceField(
        queryset=Produto.objects.filter(ativo=True),
        label='Produto',
        widget=forms.Select(attrs={'autofocus': True, 'class': _CLASSE_CAMPO}),
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
    peso_variavel = forms.TypedChoiceField(
        label='Peso variável?',
        choices=((False, 'Não'), (True, 'Sim')),
        coerce=lambda v: v == 'True',
        initial=False,
        widget=forms.Select(attrs={'class': _CLASSE_CAMPO}),
    )
    peso_kg = forms.DecimalField(
        label='Peso (kg)',
        max_digits=12,
        decimal_places=3,
        min_value=Decimal('0.001'),
        required=False,
        widget=forms.NumberInput(attrs={'step': '0.001', 'autocomplete': 'off', 'class': _CLASSE_CAMPO}),
    )

    def clean_lote(self):
        lote = self.cleaned_data['lote'].strip().upper()
        ja_aprovado = Palete.objects.filter(lote=lote).exclude(estado__in=ESTADOS_LOTE_EM_PRODUCAO).exists()
        if ja_aprovado:
            raise forms.ValidationError(
                f'O lote "{lote}" já foi aprovado e não pode receber novos paletes.'
            )
        return lote

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('peso_variavel') and not cleaned_data.get('peso_kg'):
            self.add_error('peso_kg', 'Informe o peso do palete.')
        return cleaned_data
