from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .ldap_lookup import PAPEIS, SETORES


class NovoUsuarioForm(forms.Form):
    """Cadastro no pop-up "Novo": o usuário é escolhido por busca no AD (o campo
    `username` é preenchido pelo autocomplete) e recebe grupo (setor) + subgrupo
    (papel). A existência no AD é validada na view, não aqui."""

    username = forms.CharField(widget=forms.HiddenInput)
    grupo = forms.ChoiceField(
        label='Grupo',
        choices=[(s, s) for s in SETORES],
        widget=forms.Select(attrs={'class': 'input-field py-2.5'}),
    )
    subgrupo = forms.ChoiceField(
        label='Subgrupo',
        choices=[(p, p) for p in PAPEIS],
        widget=forms.Select(attrs={'class': 'input-field py-2.5'}),
    )


class AlterarGrupoUsuarioForm(forms.Form):
    usuario_id = forms.CharField(widget=forms.HiddenInput)
    grupo = forms.ChoiceField(
        label='Grupo',
        choices=[(s, s) for s in SETORES],
        widget=forms.Select(attrs={'class': 'input-field py-2.5'}),
    )
    subgrupo = forms.ChoiceField(
        label='Subgrupo',
        choices=[(p, p) for p in PAPEIS],
        widget=forms.Select(attrs={'class': 'input-field py-2.5'}),
    )


class LoginForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        'invalid_login': (
            'Credenciais de acesso não encontradas. As credenciais devem ser '
            'as mesmas usadas para acessar a rede corporativa.'
        ),
    }
