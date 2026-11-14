"""Salon client profiles."""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import SalonScopedModel


class Client(SalonScopedModel):
    """A customer of a salon.

    Clients are per-salon (multi-tenant): the same person visiting two salons has
    two profiles. A registered user account is optional — walk-ins and phone
    bookings only need a name and a phone number.
    """

    class Language(models.TextChoices):
        GEORGIAN = "ka", _("Georgian")
        ENGLISH = "en", _("English")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_profiles",
        verbose_name=_("user account"),
    )
    full_name = models.CharField(_("full name"), max_length=120)
    phone = models.CharField(_("phone number"), max_length=32, blank=True)
    email = models.EmailField(_("email"), blank=True)
    preferred_language = models.CharField(
        _("preferred language"),
        max_length=2,
        choices=Language.choices,
        default=Language.GEORGIAN,
    )
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        ordering = ["salon", "full_name"]
        verbose_name = _("client")
        verbose_name_plural = _("clients")
        constraints = [
            models.UniqueConstraint(
                fields=["salon", "phone"],
                condition=~Q(phone=""),
                name="client_unique_phone_per_salon",
            ),
        ]

    def __str__(self) -> str:
        return self.full_name
