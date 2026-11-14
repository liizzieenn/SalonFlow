"""Shared abstract base models."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    """Adds ``created_at`` / ``updated_at`` audit fields."""

    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        abstract = True


class SalonScopedModel(TimeStampedModel):
    """Base for every tenant-owned model: each row belongs to exactly one Salon."""

    salon = models.ForeignKey(
        "salons.Salon",
        on_delete=models.CASCADE,
        related_name="%(class)ss",
        verbose_name=_("salon"),
    )

    class Meta:
        abstract = True
