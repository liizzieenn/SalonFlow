"""Create (or rebuild) the public demo salon.

    python manage.py seed_demo              # create it if missing
    python manage.py seed_demo --reset      # delete and recreate (e.g. nightly cron)

Creates a salon with three specialists, services, working hours with a lunch
break, FAQ entries in Georgian and English, ~30 clients and about two months of
past bookings plus two weeks ahead (mixed statuses, so the analytics have shape).

Demo clients deliberately have **no phone number or email**: on a live server with
a real SMS/email backend, made-up contact details could belong to real people.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import User
from apps.assistant import knowledge
from apps.assistant.models import FAQ
from apps.assistant.providers import ProviderError
from apps.assistant.signals import suppress_indexing
from apps.bookings.models import Booking
from apps.clients.models import Client
from apps.salons.models import Break, Salon, Service, Staff, TimeOff, WorkingHours

DEMO_SLUG = "demo"
OWNER_USERNAME = "demo"
STAFF_USERNAME = "demo-staff"
PAST_DAYS = 60
FUTURE_DAYS = 14
GRID = 15  # minutes

SERVICES = [
    # name, minutes, buffer, price, description
    ("ქალის თმის შეჭრა", 60, 15, "45", "დაბანა, შეჭრა და ვარცხნილობა."),
    ("კაცის თმის შეჭრა", 30, 0, "25", "კლასიკური ან თანამედროვე შეჭრა."),
    ("თმის შეღებვა", 120, 15, "120", "ერთფეროვანი შეღებვა, მოვლითი საშუალებებით."),
    ("მანიკური", 60, 0, "35", "კლასიკური მანიკური გელ-ლაქით."),
    ("პედიკური", 75, 0, "50", "აპარატული პედიკური და ფრჩხილების მოვლა."),
    ("წარბების კორექცია", 30, 0, "20", "წარბების ფორმირება და შეღებვა."),
]
STAFF = [
    # name, title, service indexes, user
    ("ანა ბერიძე", "სტილისტი", [0, 1, 2, 5], STAFF_USERNAME),
    ("მარიამ კაპანაძე", "მანიკურის სპეციალისტი", [3, 4, 5], None),
    ("ნინო გელაშვილი", "კოლორისტი", [0, 2], None),
]
FAQS = [
    ("ბარათით გადახდა შეიძლება?",
     "დიახ, ვიღებთ Visa-სა და Mastercard-ს, ასევე ნაღდ ფულს."),
    ("Can I pay by card?", "Yes, we accept Visa and Mastercard, and cash."),
    ("როგორ გავაუქმო ჯავშანი?",
     "გთხოვთ, გააუქმოთ ვიზიტამდე მინიმუმ 12 საათით ადრე — დაგვირეკეთ."),
    ("How do I cancel a booking?",
     "Please cancel at least 12 hours before your visit by calling the salon."),
    ("გაქვთ პარკინგი?", "დიახ, სალონის უკან უფასო პარკინგია."),
    ("Is there parking?", "Yes, free parking is available behind the salon."),
    ("გაქვთ სასაჩუქრე ბარათები?", "დიახ, სასაჩუქრე ბარათები 50, 100 და 200 ლარად."),
    ("Do you sell gift cards?",
     "Yes, gift cards are available for 50, 100 and 200 GEL."),
]  # fmt: skip
FIRST_NAMES = [
    "ნინო",
    "თამარ",
    "ეკა",
    "სალომე",
    "მარი",
    "ქეთი",
    "ლია",
    "ანი",
    "ნათია",
    "თეონა",
]
LAST_NAMES = ["ბერიძე", "კაპანაძე", "გელაშვილი", "ლომიძე", "ჯაფარიძე", "მაისურაძე"]


@dataclass
class Plan:
    rng: random.Random
    tz: ZoneInfo
    today: date
    now: datetime


class Command(BaseCommand):
    help = "Create the demo salon with staff, services, FAQ, clients and bookings."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--reset", action="store_true", help="Delete and recreate.")
        parser.add_argument("--seed", type=int, default=42, help="Random seed.")
        parser.add_argument(
            "--password",
            default=os.environ.get("DEMO_PASSWORD", "salonflow-demo"),
            help="Password for the demo owner and staff logins "
            "(default: $DEMO_PASSWORD or 'salonflow-demo').",
        )

    def handle(self, *args, reset: bool, seed: int, password: str, **options) -> None:
        existing = Salon.objects.filter(slug=DEMO_SLUG).first()
        if existing and not reset:
            raise CommandError("Demo salon already exists; use --reset to rebuild it.")
        # One synchronous re-index at the end instead of a task per saved object.
        with suppress_indexing(), transaction.atomic():
            if existing:
                self._delete(existing)
            salon = self._create(password, seed)
        try:
            counts = knowledge.reindex_salon(salon)
        except ProviderError as exc:  # keep the demo usable without the assistant
            self.stderr.write(f"Assistant index not built: {exc}")
            counts = {}
        self.stdout.write(
            self.style.SUCCESS(
                f"Demo salon '{salon.slug}' ready: "
                f"{Booking.objects.filter(salon=salon).count()} bookings, "
                f"assistant index {counts}.\n"
                f"Owner login: {OWNER_USERNAME} / {password}\n"
                f"Staff login: {STAFF_USERNAME} / {password}"
            )
        )

    # -- helpers ----------------------------------------------------------------------

    def _delete(self, salon: Salon) -> None:
        # Bookings PROTECT their client/staff/service, so they go first.
        Booking.objects.filter(salon=salon).delete()
        salon.delete()
        # The demo logins are recreated; other salons' users are never touched.
        User.objects.filter(
            username__in=[OWNER_USERNAME, STAFF_USERNAME], owned_salons__isnull=True
        ).delete()

    def _user(self, username: str, first_name: str, password: str) -> User:
        user, _ = User.objects.get_or_create(
            username=username,
            defaults={
                "email": f"{username}@demo.salonflow.invalid",
                "first_name": first_name,
            },
        )
        user.set_password(password)
        user.save()
        return user

    def _create(self, password: str, seed: int) -> Salon:
        tz = ZoneInfo("Asia/Tbilisi")
        now = timezone.now()
        plan = Plan(random.Random(seed), tz, now.astimezone(tz).date(), now)

        owner = self._user(OWNER_USERNAME, "ლიზი", password)
        salon = Salon.objects.create(
            name="Iris Beauty Studio",
            slug=DEMO_SLUG,
            owner=owner,
            address="რუსთაველის გამზ. 12",
            city="ქუთაისი",
            phone="+995 431 00 00 00",
            timezone="Asia/Tbilisi",
        )
        services = [
            Service.objects.create(
                salon=salon,
                name=name,
                duration_minutes=minutes,
                buffer_minutes=buffer,
                price=Decimal(price),
                description=description,
            )
            for name, minutes, buffer, price, description in SERVICES
        ]
        staff_members = []
        for name, title, service_idx, username in STAFF:
            user = self._user(username, name.split()[0], password) if username else None
            member = Staff.objects.create(
                salon=salon, full_name=name, title=title, user=user
            )
            member.services.set([services[i] for i in service_idx])
            for weekday in range(6):  # Mon–Sat
                WorkingHours.objects.create(
                    staff=member,
                    weekday=weekday,
                    start_time=time(10),
                    end_time=time(19),
                )
                Break.objects.create(
                    staff=member,
                    weekday=weekday,
                    start_time=time(14),
                    end_time=time(14, 30),
                )
            staff_members.append(member)
        # One day off next week, so the calendar and slot engine show it.
        off_day = plan.today + timedelta(days=7 - plan.today.weekday() + 2)
        TimeOff.objects.create(
            staff=staff_members[1],
            start_at=datetime.combine(off_day, time.min, tzinfo=tz),
            end_at=datetime.combine(off_day + timedelta(days=1), time.min, tzinfo=tz),
            reason="შვებულება",
        )
        for order, (question, answer) in enumerate(FAQS):
            FAQ.objects.create(
                salon=salon, question=question, answer=answer, order=order
            )
        clients = Client.objects.bulk_create(
            Client(
                salon=salon,
                full_name=(
                    f"{plan.rng.choice(FIRST_NAMES)} {plan.rng.choice(LAST_NAMES)}"
                ),
                preferred_language=plan.rng.choice(["ka", "ka", "ka", "en"]),
            )
            for _ in range(30)
        )
        self._bookings(plan, salon, staff_members, clients)
        return salon

    def _bookings(self, plan: Plan, salon, staff_members, clients) -> None:
        """Fill each specialist's days back to back with random gaps."""
        rng, tz = plan.rng, plan.tz
        bookings: list[Booking] = []
        for offset in range(-PAST_DAYS, FUTURE_DAYS + 1):
            day = plan.today + timedelta(days=offset)
            if day.weekday() == 6:
                continue
            future = offset >= 0
            for member in staff_members:
                if member.time_off.filter(
                    start_at__lte=datetime.combine(day, time(12), tzinfo=tz),
                    end_at__gt=datetime.combine(day, time(12), tzinfo=tz),
                ).exists():
                    continue
                services = list(member.services.all())
                cursor = datetime.combine(day, time(10), tzinfo=tz)
                close = datetime.combine(day, time(19), tzinfo=tz)
                lunch = (
                    datetime.combine(day, time(14), tzinfo=tz),
                    datetime.combine(day, time(14, 30), tzinfo=tz),
                )
                # Fuller in the past; leave free slots ahead for live bookings.
                gap_choices = (
                    [0, 0, 15, 30, 60] if not future else [15, 30, 60, 90, 120]
                )
                while True:
                    cursor += timedelta(minutes=rng.choice(gap_choices))
                    service = rng.choice(services)
                    end = cursor + timedelta(minutes=service.duration_minutes)
                    block_end = end + timedelta(minutes=service.buffer_minutes)
                    if block_end > close:
                        break
                    if cursor < lunch[1] and block_end > lunch[0]:
                        cursor = lunch[1]
                        continue
                    if future and cursor <= plan.now:
                        cursor = block_end
                        continue
                    bookings.append(
                        Booking(
                            salon=salon,
                            client=rng.choice(clients),
                            staff=member,
                            service=service,
                            start_at=cursor,
                            end_at=end,
                            price=service.price,
                            status=self._status(rng, end <= plan.now),
                        )
                    )
                    cursor = block_end
        # bulk_create: no signals, so no confirmation emails for demo bookings.
        Booking.objects.bulk_create(bookings, batch_size=500)
        # Bookings look like they were made a few days ahead.
        Booking.objects.filter(salon=salon).update(
            created_at=F("start_at") - timedelta(days=3)
        )

    @staticmethod
    def _status(rng: random.Random, finished: bool) -> str:
        S = Booking.Status
        if finished:
            return rng.choices(
                [S.COMPLETED, S.CONFIRMED, S.NO_SHOW, S.CANCELLED],
                weights=[80, 7, 6, 7],
            )[0]
        return rng.choices([S.CONFIRMED, S.PENDING, S.CANCELLED], weights=[85, 10, 5])[
            0
        ]
