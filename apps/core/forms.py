from django import forms


class ReautenticacaoForm(forms.Form):
    senha = forms.CharField(
        label='Senha',
        widget=forms.PasswordInput(attrs={'autofocus': True, 'autocomplete': 'current-password'}),
    )
