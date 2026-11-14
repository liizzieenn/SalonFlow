# CLAUDE.md — SalonFlow

Portfolio project for Lizi Dekanozishvili (Full-Stack Developer). Goal: a production-quality, deployed salon booking platform that looks strong on a CV and GitHub.

## Working rules
- Follow the roadmap in README.md milestone by milestone (M1 → M7). Tick checkboxes when done.
- Small, meaningful commits with clear messages (the commit history is part of the portfolio).
- Every feature gets tests (pytest + pytest-django). Keep CI green.
- Clean code: type hints, docstrings for non-trivial logic, ruff + black formatting.
- Secrets only via `.env` (never commit them). Keep `.env.example` updated.
- UI text must be translatable (`gettext`) — Georgian and English.

## Stack
Django 5 + DRF, PostgreSQL 16 + pgvector, Celery + Redis, HTMX + Tailwind, Docker.

## Planned structure
```
config/            # Django settings (base/dev/prod), urls, celery
apps/
  salons/          # Salon, Staff, WorkingHours, Service
  bookings/        # Booking, slot engine, booking flow
  clients/         # Client profiles
  notifications/   # Celery reminder tasks
  dashboard/       # owner analytics
  assistant/       # RAG AI chat (pgvector)
templates/ static/ tests/
```

## Key domain logic
- Slot engine: free slots = staff working hours − breaks − existing bookings, respecting service duration and buffer time. Must prevent double-booking (DB constraint + transaction/select_for_update).
- Multi-tenant: every model scoped to a Salon.

## Status
M1 done: project scaffold, settings split (base/dev/test/prod), custom User, domain models
(Salon, Service, Staff, WorkingHours, Break, TimeOff, Client, Booking) with admin, Docker
(web, celery, pgvector db, redis), pytest suite, GitHub Actions CI (ruff, black, migrations
check, tests on pgvector/pg16).

Notes:
- Double-booking is blocked at DB level by `booking_no_staff_overlap` (ExclusionConstraint,
  btree_gist). `Booking.end_at` excludes buffer time — the slot engine must add buffer.
- Tests require PostgreSQL (not SQLite).

## Next step
Start M2: slot engine (working hours − breaks − time off − bookings, respecting duration +
buffer), booking service with `transaction.atomic` + `select_for_update`, booking flow views.
