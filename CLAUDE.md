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

M2 done: slot engine (`apps/bookings/slots.py`), booking service with atomic +
`select_for_update` (`apps/bookings/services.py`), public HTMX booking flow at
`/s/<salon-slug>/book/` (`apps/bookings/views.py`, `templates/bookings/`).

M3 done: `apps/notifications` — `Notification` delivery log, Celery tasks
`send_booking_notification` / `dispatch_due_reminders` (beat, every 5 min), pluggable SMS
backends (`SMS_BACKEND`: console / locmem / twilio), email + SMS templates in
`templates/notifications/`. Confirmation is queued from `create_booking` via on_commit.

M4 done: `apps/dashboard` — login (Django auth views, `templates/registration/`),
access rules in `access.py` (owner: all; staff: own bookings, no analytics; others: 404),
week calendar (`calendar.py`), booking status changes (`change_status` in
`apps/bookings/services.py`), analytics queries (`analytics.py`) + Chart.js charts.

M5 done: `apps/assistant` — `FAQ` + `KnowledgeChunk` (pgvector, HNSW) models,
indexing (`knowledge.py`, Celery `tasks.py`, `signals.py`), RAG chat (`chat.py`) with
salon-bound `find_free_slots` tool, providers in `providers/` (fake / anthropic /
voyage), rate limiting (`ratelimit.py`), HTMX widget (`{% assistant_widget salon %}`).

M6 done: Georgian default (unprefixed) / English (`/en/`) via `i18n_patterns`; switcher
(`apps/core/views.set_language`); full `ka` catalogue (`.po` + committed `.mo`); clients'
`preferred_language` captured at booking and used for notifications; `tests/test_i18n.py`
gates CI on catalogue completeness.

M7 (code complete, live deploy pending): compiled Tailwind 4 (`static/src/app.css` →
`npm run build`, built in the Docker image), `docker-compose.prod.yml` (Caddy HTTPS →
gunicorn, celery, beat, db, redis) + `deploy/` (Caddyfile, deploy.sh), `docs/deploy.md`,
`manage.py seed_demo [--reset]`, deploy workflow over SSH, CI `prod-stack` job that boots
the real stack and smoke-tests it over HTTPS, README screenshots in `docs/screenshots/`.

Notes:
- Tailwind scans `templates/` and `apps/` (Python files that build class strings). After
  adding classes run `npm run build` locally (or the dev compose `css` watcher).
- Production behind Caddy: client IPs come from `apps.core.net.client_ip`
  (`TRUSTED_PROXY_HOPS`); never read `REMOTE_ADDR` directly for rate limiting.
- `seed_demo` clients have no phone/email on purpose (never message real people).
- i18n: after adding/changing any user-facing string run makemessages + translate +
  compilemessages (commands in README) — CI fails otherwise. Tests run in English by
  default (`tests/conftest.py`); Georgian behaviour lives in `tests/test_i18n.py`.
  Never `capfirst`/`upper` Georgian text (Python maps it to Mtavruli capitals).
- Assistant knowledge text is always built in English (`translation.override("en")`).
- Assistant: retrieval and the slot tool are always salon-scoped; never trust model
  output for URLs or ids from other salons. Dev/tests use fake providers (no keys).
- `EMBEDDING_DIMENSIONS` (1024) is baked into the migration; changing the embedding
  model's size needs a migration + `manage.py reindex_assistant`.
- Notifications are idempotent per (booking, kind, channel) — unique constraint + row lock.
  Reminder lead times live in `BOOKING_REMINDER_HOURS`.
- Double-booking is blocked at DB level by `booking_no_staff_overlap` (ExclusionConstraint,
  btree_gist). `Booking.end_at` excludes buffer time — the slot engine adds it.
- Slot rule: the whole `duration + buffer` block must fit in free time; starts are on a
  15-min grid (salon-local) and in the future.
- Booking views return HTMX fragments, or the full page for non-HTMX / history-restore
  requests.
- Tests require PostgreSQL (not SQLite).

## Next step
Deploy to the VPS following `docs/deploy.md`, set the GitHub deploy secrets, then put the
live URL into the README "Live demo" section and tick M7.
