# SalonFlow 💇‍♀️

**Online booking + AI assistant platform for beauty salons** — built for and piloted at a real salon in Kutaisi, Georgia.

Clients book appointments online in Georgian or English, get automatic reminders, and can ask an AI assistant about services, prices and availability. Salon owners manage staff schedules and see revenue / occupancy analytics in a dashboard.

[![CI](https://github.com/liizzieenn/salonflow/actions/workflows/ci.yml/badge.svg)](https://github.com/liizzieenn/salonflow/actions/workflows/ci.yml)

> 🚧 Work in progress — see [Roadmap](#roadmap).

## Features

**For clients**
- Browse services, prices and staff
- Book, reschedule or cancel an appointment in real-time free slots
- Email / SMS reminders 24h and 2h before the visit
- AI chat assistant (RAG) that answers questions about services and can suggest free slots
- Georgian 🇬🇪 / English 🇬🇧 interface

**For salon owners & staff**
- Staff working hours, breaks and days off
- Calendar view of all bookings
- Analytics: revenue, busiest hours, top services, no-show rate
- Multi-salon (multi-tenant) support

## Tech stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12, Django 5, Django REST Framework |
| Database | PostgreSQL 16 + pgvector |
| Background jobs | Celery + Redis (reminders, embeddings) |
| AI | RAG over salon services/FAQ, embeddings in pgvector, LLM API |
| Frontend | Django templates + HTMX + Tailwind CSS |
| Auth | django-allauth (email + Google) |
| Infra | Docker, docker-compose, GitHub Actions CI, deployed on a VPS |
| Testing | pytest, pytest-django, factory_boy |

## Architecture

```
Client (browser) ──► Django (HTMX views + REST API)
                        │
                        ├── PostgreSQL (+ pgvector)
                        ├── Redis ──► Celery workers (reminders, embeddings)
                        └── LLM API (AI assistant)
```

## Roadmap

- [x] **M1 — Core:** project setup, Docker, models (Salon, Staff, Service, Booking, Client), admin
- [x] **M2 — Booking engine:** free-slot calculation, booking flow, conflict prevention, tests
- [x] **M3 — Notifications:** Celery + Redis, email/SMS reminders
- [x] **M4 — Dashboard:** owner calendar + analytics charts
- [ ] **M5 — AI assistant:** pgvector embeddings, RAG chat over services/FAQ, slot suggestions
- [ ] **M6 — i18n:** Georgian / English
- [ ] **M7 — Ship:** CI, deployment, demo data, screenshots, live demo link

## Getting started

### With Docker (recommended)

```bash
git clone https://github.com/liizzieenn/salonflow.git
cd salonflow
cp .env.example .env
docker compose up --build
docker compose exec web python manage.py createsuperuser
```

App: http://localhost:8000 · Booking page: http://localhost:8000/s/&lt;salon-slug&gt;/book/ · Dashboard: http://localhost:8000/dashboard/ · Admin: http://localhost:8000/admin/ · Health: http://localhost:8000/health/

### Without Docker

Requires Python 3.12+, PostgreSQL 16 and Redis.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env   # point DATABASE_URL / REDIS_URL at localhost
python manage.py migrate
python manage.py runserver
```

### Tests & code quality

```bash
pytest                 # runs against PostgreSQL (needed for exclusion constraints)
pytest --cov           # with coverage
ruff check . && black --check .
```

## Project structure

```
config/              settings (base / dev / test / prod), urls, celery app
apps/
  core/              shared abstract models (timestamps, salon scoping), health check
  accounts/          custom User model
  salons/            Salon, Service, Staff, WorkingHours, Break, TimeOff
  clients/           per-salon Client profiles
  bookings/          Booking, slot engine, booking service, HTMX booking flow
  notifications/     confirmation + reminder tasks, pluggable SMS backends
  dashboard/         owner/staff calendar, booking status, analytics
  assistant/         RAG AI chat (M5)
tests/               pytest suite + factory_boy factories
docker/              container entrypoint
```

### Booking engine

- **Slot engine** (`apps/bookings/slots.py`): free time = working hours − breaks −
  time off − existing bookings (each extended by its service's buffer). A start time is
  offered when the whole `duration + buffer` block fits, it lies on the salon's 15-minute
  grid and it is in the future. "Any specialist" merges every staff member's slots.
- **Booking service** (`apps/bookings/services.py`): `create_booking` runs in
  `transaction.atomic`, locks the staff row with `select_for_update()` and re-checks
  availability before inserting, so concurrent requests for the same staff member are
  serialised. A threaded test fires 8 simultaneous requests at one slot: exactly one wins.
- **Booking flow** (`apps/bookings/views.py`): service → specialist → day → time →
  details → confirmation. Every step is an HTMX fragment (`hx-boost`); without
  JavaScript or on refresh the same URL renders the full page.

### Notifications

- **Confirmation** email + SMS is queued with `transaction.on_commit` when a booking is
  created (never for a rolled-back booking; a broker outage doesn't fail the request).
- **Reminders 24h and 2h before** the visit: Celery beat runs
  `dispatch_due_reminders` every 5 minutes and queues every reminder whose send time has
  come. A reminder is skipped for cancelled bookings and for bookings made after its send
  time (the confirmation already covered them).
- **Exactly-once delivery:** every (booking, kind, channel) gets a `Notification` row
  (unique constraint) that is row-locked while sending. Retries (exponential backoff) and
  duplicate tasks never send twice, and a retry only re-sends the failed channel.
- **Pluggable SMS** (`apps/notifications/sms/`), configured like Django's email backends
  with `SMS_BACKEND`: `ConsoleBackend` (dev, prints to the worker log), `LocMemBackend`
  (tests) and `TwilioBackend` (production). A new provider only needs to implement
  `send_messages()`.
- Messages are rendered in the client's preferred language and the salon's time zone.

### Dashboard

- **Access control** (`apps/dashboard/access.py`): owners see their salon's whole
  calendar and analytics. Staff with a linked login see only their own bookings, and
  revenue analytics are owner-only (403). Anyone else gets a 404, so a salon's existence
  isn't revealed.
- **Week calendar:** bookings are positioned on a day × time grid in the salon's time
  zone. In the all-staff view each specialist has a fixed column; there is also a
  per-specialist filter. Clicking a booking opens a detail panel (HTMX) where staff can
  mark it completed or no-show, or cancel it, and the grid refreshes itself.
- **Analytics** (`apps/dashboard/analytics.py`): revenue, bookings, average visit,
  no-show and cancellation rates with change vs. the previous period, plus charts
  (Chart.js) for revenue per day, busiest hours and top services. Every chart also has
  a table view. All aggregation runs in PostgreSQL, grouped by the salon's local day
  and hour. Revenue counts completed visits plus past visits not flagged as no-show or
  cancelled.

### Data integrity highlights

- **No double-booking, enforced by PostgreSQL:** an `EXCLUDE USING gist` constraint
  (`btree_gist`) rejects any two non-cancelled bookings of the same staff member whose
  `[start, end)` ranges overlap — safe even under concurrent requests.
- **Multi-tenant by design:** every domain model is scoped to a `Salon`; model
  validation rejects bookings that mix clients, staff or services from different salons.
- Check constraints for valid time ranges, durations and prices; unique client phone
  per salon.

## Author

**Lizi Dekanozishvili** — Full-Stack Developer · Kutaisi, Georgia
