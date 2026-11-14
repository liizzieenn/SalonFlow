from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.urls import include, path

from apps.core.views import health, set_language

# Not translated: admin, health probe and the language switch endpoint.
urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("i18n/setlang/", set_language, name="set_language"),  # language switcher
]

# Georgian (default) is served without a prefix, English under /en/.
urlpatterns += i18n_patterns(
    path("accounts/", include("django.contrib.auth.urls")),
    path("dashboard/", include("apps.dashboard.urls")),
    path("s/<slug:salon_slug>/book/", include("apps.bookings.urls")),
    path("s/<slug:salon_slug>/assistant/", include("apps.assistant.urls")),
    prefix_default_language=False,
)
