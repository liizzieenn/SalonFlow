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

## Next step
Start M1: create Django project in `config/`, the apps above, Dockerfile + docker-compose (web, db with pgvector, redis, celery), settings split, first models + admin, pytest setup, GitHub Actions CI.
