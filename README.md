# SalonFlow 💇‍♀️

**Online booking + AI assistant platform for beauty salons** — built for and piloted at a real salon in Kutaisi, Georgia.

Clients book appointments online in Georgian or English, get automatic reminders, and can ask an AI assistant about services, prices and availability. Salon owners manage staff schedules and see revenue / occupancy analytics in a dashboard.

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

- [ ] **M1 — Core:** project setup, Docker, models (Salon, Staff, Service, Booking, Client), admin
- [ ] **M2 — Booking engine:** free-slot calculation, booking flow, conflict prevention, tests
- [ ] **M3 — Notifications:** Celery + Redis, email/SMS reminders
- [ ] **M4 — Dashboard:** owner calendar + analytics charts
- [ ] **M5 — AI assistant:** pgvector embeddings, RAG chat over services/FAQ, slot suggestions
- [ ] **M6 — i18n:** Georgian / English
- [ ] **M7 — Ship:** CI, deployment, demo data, screenshots, live demo link

## Getting started

```bash
git clone https://github.com/liizzieenn/salonflow.git
cd salonflow
cp .env.example .env
docker compose up --build
```

App: http://localhost:8000

## Author

**Lizi Dekanozishvili** — Full-Stack Developer · Kutaisi, Georgia
