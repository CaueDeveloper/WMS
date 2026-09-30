from django import forms

from .ldap_lookup import GRUPOS_ATRIBUIVEIS


class AtribuicaoUsuarioADForm(forms.Form):
    username = forms.CharField(widget=forms.HiddenInput())
    nome = forms.CharField(widget=forms.HiddenInput())
    grupo = forms.ChoiceField(
        label='Grupo',
        choices=[(nome, nome) for nome in GRUPOS_ATRIBUIVEIS],
        widget=forms.Select(attrs={'class': 'input-field py-2.5'}),
    )
