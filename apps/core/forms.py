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
        widget=forms.Select(attrs={'class': 'input-field'}),
    )
    subgrupo = forms.ChoiceField(
        label='Subgrupo',
        choices=[(p, p) for p in PAPEIS],
        widget=forms.Select(attrs={'class': 'input-field'}),
    )


class NovoOperadorForm(forms.Form):
    """Operador cadastrado direto no WMS (fora do AD): login e senha locais."""

    nome = forms.CharField(label='Nome', max_length=150, widget=forms.TextInput(attrs={'class': 'input-field'}))
    username = forms.CharField(label='Usuário (login)', max_length=150, widget=forms.TextInput(attrs={'class': 'input-field', 'autocomplete': 'off'}))
    senha = forms.CharField(
        label='Senha', min_length=6, widget=forms.PasswordInput(attrs={'class': 'input-field', 'autocomplete': 'new-password'}),
    )
    grupo = forms.ChoiceField(
        label='Grupo',
        choices=[(s, s) for s in SETORES],
        widget=forms.Select(attrs={'class': 'input-field'}),
    )


class AlterarGrupoUsuarioForm(forms.Form):
    usuario_id = forms.CharField(widget=forms.HiddenInput)
    grupo = forms.ChoiceField(
        label='Grupo',
        choices=[(s, s) for s in SETORES],
        widget=forms.Select(attrs={'class': 'input-field'}),
    )
    subgrupo = forms.ChoiceField(
        label='Subgrupo',
        choices=[(p, p) for p in PAPEIS],
        widget=forms.Select(attrs={'class': 'input-field'}),
    )


class LoginForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        'invalid_login': (
            'Credenciais de acesso não encontradas. Gestores usam as credenciais da '
            'rede corporativa; operadores, o usuário e a senha cadastrados no WMS.'
        ),
    }
