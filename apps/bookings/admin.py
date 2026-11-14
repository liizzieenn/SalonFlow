from django.contrib import admin

from .models import Booking


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "start_at",
        "end_at",
        "client",
        "staff",
        "service",
        "status",
        "price",
    )
    list_filter = ("status", "salon", "staff")
    search_fields = ("client__full_name", "client__phone", "staff__full_name")
    date_hierarchy = "start_at"
    list_select_related = ("client", "staff", "service")
    autocomplete_fields = ("client", "staff", "service")
    readonly_fields = ("created_at", "updated_at")
