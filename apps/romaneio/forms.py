from django import forms
from django.utils import timezone


class RomaneioForm(forms.Form):
    data = forms.DateField(widget=forms.DateInput(attrs={'type': 'date', 'class': 'input-field'}, format='%Y-%m-%d'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault('data', timezone.localdate())
