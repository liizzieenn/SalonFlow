import re

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.salons.models import Salon, Service, Staff

PHONE_RE = re.compile(r"^\+?\d{9,15}$")
INPUT_CLASSES = (
    "w-full rounded-lg border border-stone-300 px-3 py-2 "
    "focus:border-rose-400 focus:outline-none focus:ring-1 focus:ring-rose-400"
)


class BookingForm(forms.Form):
    """Final step of the booking flow: selected slot + client contact details."""

    service = forms.ModelChoiceField(
        queryset=Service.objects.none(), widget=forms.HiddenInput
    )
    staff = forms.ModelChoiceField(
        queryset=Staff.objects.none(), required=False, widget=forms.HiddenInput
    )
    start_at = forms.DateTimeField(widget=forms.HiddenInput)
    full_name = forms.CharField(label=_("Full name"), max_length=120)
    phone = forms.CharField(
        label=_("Phone number"),
        max_length=32,
        help_text=_("We will use it to remind you about the visit."),
    )
    email = forms.EmailField(label=_("Email (optional)"), required=False)
    notes = forms.CharField(
        label=_("Notes (optional)"),
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, salon: Salon, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = Service.objects.filter(
            salon=salon, is_active=True
        )
        self.fields["staff"].queryset = Staff.objects.filter(
            salon=salon, is_active=True
        )
        for field in self.visible_fields():
            field.field.widget.attrs.setdefault("class", INPUT_CLASSES)
        self.fields["phone"].widget.attrs.update(
            {"type": "tel", "autocomplete": "tel", "inputmode": "tel"}
        )
        self.fields["full_name"].widget.attrs["autocomplete"] = "name"
        self.fields["email"].widget.attrs["autocomplete"] = "email"

    def clean_phone(self) -> str:
        phone = re.sub(r"[\s\-()]", "", self.cleaned_data["phone"])
        if not PHONE_RE.match(phone):
            raise forms.ValidationError(
                _("Enter a valid phone number, e.g. +995555123456.")
            )
        return phone

    def clean_full_name(self) -> str:
        return " ".join(self.cleaned_data["full_name"].split())
