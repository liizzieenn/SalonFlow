"""Salon tenant and its catalogue: staff, services and schedules."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import SalonScopedModel, TimeStampedModel


class Weekday(models.IntegerChoices):
    """ISO-like weekday numbering matching ``datetime.date.weekday()``."""

    MONDAY = 0, _("Monday")
    TUESDAY = 1, _("Tuesday")
    WEDNESDAY = 2, _("Wednesday")
    THURSDAY = 3, _("Thursday")
    FRIDAY = 4, _("Friday")
    SATURDAY = 5, _("Saturday")
    SUNDAY = 6, _("Sunday")


class Salon(TimeStampedModel):
    """A beauty salon — the tenant every other domain object is scoped to."""

    name = models.CharField(_("name"), max_length=120)
    slug = models.SlugField(_("slug"), max_length=140, unique=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_salons",
        verbose_name=_("owner"),
    )
    address = models.CharField(_("address"), max_length=255, blank=True)
    city = models.CharField(_("city"), max_length=80, blank=True)
    phone = models.CharField(_("phone number"), max_length=32, blank=True)
    email = models.EmailField(_("email"), blank=True)
    timezone = models.CharField(_("time zone"), max_length=64, default="Asia/Tbilisi")
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("salon")
        verbose_name_plural = _("salons")

    def __str__(self) -> str:
        return self.name


class Service(SalonScopedModel):
    """A bookable service (haircut, manicure, ...) with a fixed duration."""

    name = models.CharField(_("name"), max_length=120)
    description = models.TextField(_("description"), blank=True)
    duration_minutes = models.PositiveSmallIntegerField(
        _("duration (minutes)"), validators=[MinValueValidator(5)]
    )
    buffer_minutes = models.PositiveSmallIntegerField(
        _("buffer after (minutes)"),
        default=0,
        help_text=_("Clean-up / preparation time blocked after the appointment."),
    )
    price = models.DecimalField(
        _("price (GEL)"),
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["salon", "name"]
        verbose_name = _("service")
        verbose_name_plural = _("services")
        constraints = [
            models.UniqueConstraint(
                fields=["salon", "name"], name="service_unique_name_per_salon"
            ),
            models.CheckConstraint(
                condition=Q(duration_minutes__gte=5),
                name="service_min_duration",
            ),
            models.CheckConstraint(
                condition=Q(price__gte=0), name="service_price_gte_0"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.duration_minutes} min)"


class Staff(SalonScopedModel):
    """A stylist / master working at a salon who can perform services."""

    salon = models.ForeignKey(
        Salon,
        on_delete=models.CASCADE,
        related_name="staff_members",
        verbose_name=_("salon"),
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff_profile",
        verbose_name=_("user account"),
    )
    full_name = models.CharField(_("full name"), max_length=120)
    title = models.CharField(_("title"), max_length=80, blank=True)
    bio = models.TextField(_("bio"), blank=True)
    services = models.ManyToManyField(
        Service, related_name="staff", blank=True, verbose_name=_("services")
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        ordering = ["salon", "full_name"]
        verbose_name = _("staff member")
        verbose_name_plural = _("staff")

    def __str__(self) -> str:
        return self.full_name


class _WeeklyInterval(models.Model):
    """Abstract recurring weekly time interval for a staff member."""

    weekday = models.PositiveSmallIntegerField(_("weekday"), choices=Weekday.choices)
    start_time = models.TimeField(_("start time"))
    end_time = models.TimeField(_("end time"))

    class Meta:
        abstract = True
        ordering = ["staff", "weekday", "start_time"]

    def __str__(self) -> str:
        return (
            f"{self.get_weekday_display()} "
            f"{self.start_time:%H:%M}–{self.end_time:%H:%M}"
        )

    def clean(self) -> None:
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": _("End time must be after start time.")})


class WorkingHours(_WeeklyInterval):
    """A shift: when a staff member works on a given weekday.

    Several rows per weekday are allowed (split shifts).
    """

    staff = models.ForeignKey(
        Staff,
        on_delete=models.CASCADE,
        related_name="working_hours",
        verbose_name=_("staff member"),
    )

    class Meta(_WeeklyInterval.Meta):
        verbose_name = _("working hours")
        verbose_name_plural = _("working hours")
        constraints = [
            models.CheckConstraint(
                condition=Q(end_time__gt=F("start_time")),
                name="working_hours_end_after_start",
            ),
            models.UniqueConstraint(
                fields=["staff", "weekday", "start_time"],
                name="working_hours_unique_shift_start",
            ),
        ]


class Break(_WeeklyInterval):
    """A recurring break (e.g. lunch) inside a staff member's working hours."""

    staff = models.ForeignKey(
        Staff,
        on_delete=models.CASCADE,
        related_name="breaks",
        verbose_name=_("staff member"),
    )

    class Meta(_WeeklyInterval.Meta):
        verbose_name = _("break")
        verbose_name_plural = _("breaks")
        constraints = [
            models.CheckConstraint(
                condition=Q(end_time__gt=F("start_time")),
                name="break_end_after_start",
            ),
        ]


class TimeOff(TimeStampedModel):
    """A one-off absence (vacation, sick day) blocking a datetime range."""

    staff = models.ForeignKey(
        Staff,
        on_delete=models.CASCADE,
        related_name="time_off",
        verbose_name=_("staff member"),
    )
    start_at = models.DateTimeField(_("starts at"))
    end_at = models.DateTimeField(_("ends at"))
    reason = models.CharField(_("reason"), max_length=255, blank=True)

    class Meta:
        ordering = ["staff", "start_at"]
        verbose_name = _("time off")
        verbose_name_plural = _("time off")
        constraints = [
            models.CheckConstraint(
                condition=Q(end_at__gt=F("start_at")),
                name="time_off_end_after_start",
            ),
        ]

    def clean(self) -> None:
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValidationError({"end_at": _("End must be after start.")})

    def __str__(self) -> str:
        return f"{self.staff}: {self.start_at:%Y-%m-%d} → {self.end_at:%Y-%m-%d}"
