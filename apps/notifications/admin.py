from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "booking",
        "kind",
        "channel",
        "recipient",
        "status",
        "attempts",
        "sent_at",
    )
    list_filter = ("kind", "channel", "status")
    search_fields = ("recipient", "booking__client__full_name")
    list_select_related = ("booking__client", "booking__service")
    readonly_fields = [f.name for f in Notification._meta.fields]

    def has_add_permission(self, request) -> bool:
        return False
