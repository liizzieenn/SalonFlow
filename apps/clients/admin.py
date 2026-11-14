from django.contrib import admin

from .models import Client


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("full_name", "salon", "phone", "email", "preferred_language")
    list_filter = ("salon", "preferred_language")
    search_fields = ("full_name", "phone", "email")
    list_select_related = ("salon",)
