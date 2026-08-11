from django import forms

from apps.estoque.models import Produto


class RecebimentoForm(forms.Form):
    produto = forms.ModelChoiceField(
        queryset=Produto.objects.filter(ativo=True),
        label='Produto',
        widget=forms.Select(attrs={'autofocus': True}),
    )
    lote = forms.CharField(
        label='Lote',
        max_length=60,
        widget=forms.TextInput(attrs={'autocomplete': 'off'}),
    )

    def clean_lote(self):
        return self.cleaned_data['lote'].strip().upper()
