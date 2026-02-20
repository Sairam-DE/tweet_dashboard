import re
from datetime import date

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(required=False)

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")


class CollectQueryForm(forms.Form):
    query = forms.CharField(max_length=300, widget=forms.TextInput(attrs={"placeholder": "pawan kalyan"}))
    since = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    until = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    event = forms.CharField(
        max_length=80,
        widget=forms.TextInput(attrs={"placeholder": "iphone"}),
        help_text="Event name used as the dataset folder.",
    )
    bearer_token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=True, attrs={"placeholder": "Optional: X_BEARER_TOKEN override"}),
        help_text="Leave empty to use server environment token.",
    )

    def clean_event(self):
        event = self.cleaned_data["event"].strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", event):
            raise forms.ValidationError("Event can only contain letters, numbers, underscores, and hyphens.")
        return event

    def clean(self):
        cleaned_data = super().clean()
        since = cleaned_data.get("since")
        until = cleaned_data.get("until")

        if since and until and until < since:
            self.add_error("until", "Until date must be greater than or equal to since date.")
        if since and since > date.today():
            self.add_error("since", "Since date cannot be in the future.")
        return cleaned_data

