"""Celery tasks keeping the knowledge index in sync with salon data."""

from __future__ import annotations

from celery import shared_task

from apps.salons.models import Salon

from . import knowledge
from .providers import ProviderError


@shared_task(
    name="assistant.index_source",
    autoretry_for=(ProviderError,),
    retry_backoff=30,
    retry_backoff_max=600,
    max_retries=5,
)
def index_source(source: str, source_id: int) -> str:
    return knowledge.index_source(source, source_id)


@shared_task(name="assistant.reindex_salon")
def reindex_salon(salon_id: int) -> dict[str, int]:
    salon = Salon.objects.filter(pk=salon_id).first()
    return knowledge.reindex_salon(salon) if salon else {}
