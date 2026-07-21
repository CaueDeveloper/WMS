from django import forms

from apps.estoque.models import Produto


class RecebimentoForm(forms.Form):
    produto = forms.ModelChoiceField(
        queryset=Produto.objects.filter(ativo=True),
        label='Produto',
        widget=forms.Select(attrs={'autofocus': True}),
    )
    peso_kg = forms.DecimalField(
        label='Peso (kg)',
        max_digits=12,
        decimal_places=3,
        widget=forms.TextInput(attrs={'inputmode': 'decimal', 'placeholder': '0,000', 'autocomplete': 'off'}),
    )
