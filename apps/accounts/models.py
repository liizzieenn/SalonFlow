from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """Custom user model (set up front so auth can evolve without painful migrations).

    Salon owners, staff and registered clients all log in with this model.
    """

    email = models.EmailField(_("email address"), unique=True)
    phone = models.CharField(_("phone number"), max_length=32, blank=True)

    class Meta(AbstractUser.Meta):
        verbose_name = _("user")
        verbose_name_plural = _("users")
