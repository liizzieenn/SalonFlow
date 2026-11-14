from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = (
        *BaseUserAdmin.fieldsets,
        (_("Contact"), {"fields": ("phone",)}),
    )
    list_display = ("username", "email", "first_name", "last_name", "is_staff")
