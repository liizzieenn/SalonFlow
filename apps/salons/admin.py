from django import forms
from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from .models import Break, Salon, Service, Staff, TimeOff, WorkingHours


class ServiceInline(admin.TabularInline):
    model = Service
    extra = 0
    fields = ("name", "duration_minutes", "buffer_minutes", "price", "is_active")


@admin.register(Salon)
class SalonAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "owner", "phone", "is_active", "created_at")
    list_filter = ("is_active", "city")
    search_fields = ("name", "slug", "address", "owner__email")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("owner",)
    inlines = (ServiceInline,)
    actions = ("rebuild_assistant_index",)

    @admin.action(description=_("Rebuild AI assistant index"))
    def rebuild_assistant_index(self, request, queryset) -> None:
        from apps.assistant.tasks import reindex_salon

        for salon in queryset:
            reindex_salon.delay(salon.pk)
        self.message_user(request, _("Queued assistant re-indexing."), messages.INFO)


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "salon", "duration_minutes", "buffer_minutes", "price")
    list_filter = ("salon", "is_active")
    search_fields = ("name", "description")
    list_select_related = ("salon",)


class WorkingHoursInline(admin.TabularInline):
    model = WorkingHours
    extra = 0


class BreakInline(admin.TabularInline):
    model = Break
    extra = 0


class TimeOffInline(admin.TabularInline):
    model = TimeOff
    extra = 0


class StaffAdminForm(forms.ModelForm):
    class Meta:
        model = Staff
        fields = ("salon", "user", "full_name", "title", "bio", "services", "is_active")

    def clean(self) -> dict:
        """Only allow services from the staff member's own salon (tenant isolation)."""
        cleaned = super().clean()
        salon = cleaned.get("salon")
        services = cleaned.get("services")
        if salon and services:
            foreign = [s.name for s in services if s.salon_id != salon.pk]
            if foreign:
                self.add_error(
                    "services",
                    _("Services from another salon: %(names)s")
                    % {"names": ", ".join(foreign)},
                )
        return cleaned


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    form = StaffAdminForm
    list_display = ("full_name", "salon", "title", "is_active")
    list_filter = ("salon", "is_active")
    search_fields = ("full_name", "title")
    filter_horizontal = ("services",)
    list_select_related = ("salon",)
    inlines = (WorkingHoursInline, BreakInline, TimeOffInline)
