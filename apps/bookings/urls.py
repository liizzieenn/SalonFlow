from django.urls import path

from . import views

app_name = "bookings"

urlpatterns = [
    path("", views.services_step, name="services"),
    path("staff/", views.staff_step, name="staff"),
    path("dates/", views.dates_step, name="dates"),
    path("slots/", views.slots_step, name="slots"),
    path("details/", views.details_step, name="details"),
    path("confirm/", views.confirm, name="confirm"),
]
