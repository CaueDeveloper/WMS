from django import forms

from .ldap_lookup import AREAS_AD

_CLASSE_CAMPO = 'input-field input-field-lg'


class ReautenticacaoForm(forms.Form):
    senha = forms.CharField(
        label='Senha',
        widget=forms.PasswordInput(attrs={
            'autofocus': True, 'autocomplete': 'current-password', 'class': _CLASSE_CAMPO,
        }),
    )


class AtribuicaoUsuarioADForm(forms.Form):
    area = forms.ChoiceField(
        label='Área',
        choices=[(chave, dados['label']) for chave, dados in AREAS_AD.items()],
        widget=forms.Select(attrs={'autofocus': True, 'class': _CLASSE_CAMPO}),
    )
    username = forms.CharField(widget=forms.HiddenInput())
    nome = forms.CharField(widget=forms.HiddenInput())
    papel = forms.ChoiceField(
        label='Papel',
        choices=[('GESTOR', 'Gestor'), ('COLABORADOR', 'Colaborador')],
        widget=forms.Select(attrs={'class': _CLASSE_CAMPO}),
    )
