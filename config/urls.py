from django.contrib import admin
from django.urls import include, path

from apps.core.views import health

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("s/<slug:salon_slug>/book/", include("apps.bookings.urls")),
]
