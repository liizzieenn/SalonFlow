from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("<slug:salon_slug>/", views.calendar, name="calendar"),
    path("<slug:salon_slug>/analytics/", views.analytics, name="analytics"),
    path("<slug:salon_slug>/bookings/<int:pk>/", views.booking_detail, name="booking"),
    path(
        "<slug:salon_slug>/bookings/<int:pk>/status/",
        views.booking_status,
        name="booking_status",
    ),
]
