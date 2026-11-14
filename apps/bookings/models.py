"""Bookings and the database-level guarantee against double-booking."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import (
    DateTimeRangeField,
    RangeBoundary,
    RangeOperators,
)
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Func, Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import SalonScopedModel


class TsTzRange(Func):
    """SQL ``tstzrange(start, end, '[)')`` used by the exclusion constraint."""

    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class Booking(SalonScopedModel):
    """An appointment of a client with a staff member for one service.

    ``end_at`` is the end of the service itself; buffer time is handled by the slot
    engine. Overlapping *active* bookings for the same staff member are rejected by
    a PostgreSQL exclusion constraint, so double-booking is impossible even under
    concurrent requests.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        CONFIRMED = "confirmed", _("Confirmed")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")
        NO_SHOW = "no_show", _("No-show")

    #: Statuses that free the time slot again.
    INACTIVE_STATUSES = (Status.CANCELLED,)

    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.PROTECT,
        related_name="bookings",
        verbose_name=_("client"),
    )
    staff = models.ForeignKey(
        "salons.Staff",
        on_delete=models.PROTECT,
        related_name="bookings",
        verbose_name=_("staff member"),
    )
    service = models.ForeignKey(
        "salons.Service",
        on_delete=models.PROTECT,
        related_name="bookings",
        verbose_name=_("service"),
    )
    start_at = models.DateTimeField(_("starts at"), db_index=True)
    end_at = models.DateTimeField(_("ends at"))
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    price = models.DecimalField(
        _("price (GEL)"),
        max_digits=8,
        decimal_places=2,
        help_text=_("Price snapshot at booking time."),
    )
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        ordering = ["-start_at"]
        verbose_name = _("booking")
        verbose_name_plural = _("bookings")
        indexes = [
            models.Index(fields=["salon", "start_at"], name="booking_salon_start_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_at__gt=F("start_at")),
                name="booking_end_after_start",
            ),
            ExclusionConstraint(
                name="booking_no_staff_overlap",
                expressions=[
                    (
                        TsTzRange("start_at", "end_at", RangeBoundary()),
                        RangeOperators.OVERLAPS,
                    ),
                    ("staff", RangeOperators.EQUAL),
                ],
                condition=~Q(status__in=["cancelled"]),
                violation_error_message=_(
                    "This staff member already has a booking at that time."
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.client} · {self.service.name} · {self.start_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs) -> None:
        """Fill ``end_at`` and ``price`` from the service when not given explicitly."""
        if self.service_id:
            if self.start_at and not self.end_at:
                self.end_at = self.start_at + timedelta(
                    minutes=self.service.duration_minutes
                )
            if self.price is None:
                self.price = self.service.price
        super().save(*args, **kwargs)

    def clean(self) -> None:
        """Enforce tenant isolation and that the staff member offers the service."""
        errors: dict[str, str] = {}
        salon_id = self.salon_id
        if salon_id:
            for field in ("client", "staff", "service"):
                obj = (
                    getattr(self, field, None) if getattr(self, f"{field}_id") else None
                )
                if obj is not None and obj.salon_id != salon_id:
                    errors[field] = _("Must belong to the same salon as the booking.")
        if (
            self.staff_id
            and self.service_id
            and "staff" not in errors
            and "service" not in errors
            and not self.staff.services.filter(pk=self.service_id).exists()
        ):
            errors["service"] = _("This staff member does not offer this service.")
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            errors["end_at"] = _("End must be after start.")
        if errors:
            raise ValidationError(errors)

    @property
    def is_active(self) -> bool:
        return self.status not in self.INACTIVE_STATUSES
