"""factory_boy factories for the domain models."""

from datetime import time, timedelta
from decimal import Decimal

import factory
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.clients.models import Client
from apps.salons.models import Salon, Service, Staff, Weekday, WorkingHours


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")
    password = factory.django.Password("password123")


class SalonFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Salon

    name = factory.Sequence(lambda n: f"Salon {n}")
    slug = factory.Sequence(lambda n: f"salon-{n}")
    owner = factory.SubFactory(UserFactory)
    city = "Kutaisi"


class ServiceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Service

    salon = factory.SubFactory(SalonFactory)
    name = factory.Sequence(lambda n: f"Service {n}")
    duration_minutes = 60
    buffer_minutes = 10
    price = Decimal("50.00")


class StaffFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Staff
        skip_postgeneration_save = True

    salon = factory.SubFactory(SalonFactory)
    full_name = factory.Sequence(lambda n: f"Stylist {n}")

    @factory.post_generation
    def services(self, create: bool, extracted, **kwargs) -> None:
        if create and extracted:
            self.services.set(extracted)


class WorkingHoursFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WorkingHours

    staff = factory.SubFactory(StaffFactory)
    weekday = Weekday.MONDAY
    start_time = time(10, 0)
    end_time = time(19, 0)


class ClientFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Client

    salon = factory.SubFactory(SalonFactory)
    full_name = factory.Sequence(lambda n: f"Client {n}")
    phone = factory.Sequence(lambda n: f"+99555500{n:04d}")


class BookingFactory(factory.django.DjangoModelFactory):
    """Builds a consistent booking: client, staff and service share one salon."""

    class Meta:
        model = Booking
        skip_postgeneration_save = True

    salon = factory.SubFactory(SalonFactory)
    service = factory.SubFactory(ServiceFactory, salon=factory.SelfAttribute("..salon"))
    staff = factory.SubFactory(StaffFactory, salon=factory.SelfAttribute("..salon"))
    client = factory.SubFactory(ClientFactory, salon=factory.SelfAttribute("..salon"))
    start_at = factory.LazyFunction(
        lambda: (timezone.now() + timedelta(days=1)).replace(
            minute=0, second=0, microsecond=0
        )
    )

    @factory.post_generation
    def staff_offers_service(self, create: bool, extracted, **kwargs) -> None:
        if create:
            self.staff.services.add(self.service)
