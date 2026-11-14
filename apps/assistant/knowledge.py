"""Builds, stores and searches the per-salon knowledge index.

Sources: each active service (name, price, duration, specialists, description),
each active FAQ entry, and one "salon info" chunk (address, contacts, opening
hours). ``index_source`` is idempotent: the text is hashed together with the
embedding model, so unchanged sources are not re-embedded.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.db.models import Max, Min
from pgvector.django import CosineDistance

from apps.salons.models import Salon, Service, Weekday, WorkingHours

from .models import FAQ, KnowledgeChunk
from .providers import ContextItem, get_embeddings

Source = KnowledgeChunk.Source


@dataclass(frozen=True)
class Document:
    salon_id: int
    title: str
    content: str


def _service_document(service: Service) -> Document | None:
    if not service.is_active:
        return None
    staff = ", ".join(
        service.staff.filter(is_active=True)
        .order_by("full_name")
        .values_list("full_name", flat=True)
    )
    lines = [
        f"Service: {service.name}",
        f"Price: {service.price} GEL",
        f"Duration: {service.duration_minutes} minutes",
    ]
    if staff:
        lines.append(f"Specialists: {staff}")
    if service.description:
        lines.append(service.description)
    return Document(service.salon_id, service.name, "\n".join(lines))


def _faq_document(faq: FAQ) -> Document | None:
    if not faq.is_active:
        return None
    return Document(faq.salon_id, faq.question, f"Q: {faq.question}\nA: {faq.answer}")


def opening_hours(salon: Salon) -> list[str]:
    """Per weekday: earliest start to latest end across active staff."""
    rows = (
        WorkingHours.objects.filter(staff__salon=salon, staff__is_active=True)
        .values("weekday")
        .annotate(start=Min("start_time"), end=Max("end_time"))
        .order_by("weekday")
    )
    hours = {r["weekday"]: f"{r['start']:%H:%M}–{r['end']:%H:%M}" for r in rows}
    return [
        f"{Weekday(day).label}: {hours.get(day, 'closed')}" for day in Weekday.values
    ]


def _salon_document(salon: Salon) -> Document | None:
    if not salon.is_active:
        return None
    lines = [f"Salon: {salon.name}"]
    address = ", ".join(p for p in (salon.address, salon.city) if p)
    if address:
        lines.append(f"Address: {address}")
    if salon.phone:
        lines.append(f"Phone: {salon.phone}")
    if salon.email:
        lines.append(f"Email: {salon.email}")
    lines.append("Opening hours: " + "; ".join(opening_hours(salon)))
    return Document(
        salon.pk, f"{salon.name} — contact & opening hours", "\n".join(lines)
    )


def build_document(source: str, source_id: int) -> Document | None:
    """The text to embed for a source, or ``None`` if it should not be indexed."""
    if source == Source.SERVICE:
        obj = Service.objects.filter(pk=source_id).select_related("salon").first()
        return _service_document(obj) if obj and obj.salon.is_active else None
    if source == Source.FAQ:
        obj = FAQ.objects.filter(pk=source_id).select_related("salon").first()
        return _faq_document(obj) if obj and obj.salon.is_active else None
    if source == Source.SALON:
        obj = Salon.objects.filter(pk=source_id).first()
        return _salon_document(obj) if obj else None
    raise ValueError(f"Unknown knowledge source {source!r}")


def _hash(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def index_source(source: str, source_id: int) -> str:
    """Create, update or delete the chunk for one source.

    Returns ``"created"``, ``"updated"``, ``"unchanged"`` or ``"deleted"``.
    """
    document = build_document(source, source_id)
    existing = KnowledgeChunk.objects.filter(source=source, source_id=source_id).first()
    if document is None:
        if existing:
            existing.delete()
            return "deleted"
        return "unchanged"

    embeddings = get_embeddings()
    digest = _hash(document.content, embeddings.model)
    if existing and existing.content_hash == digest:
        return "unchanged"
    vector = embeddings.embed_documents([document.content])[0]
    KnowledgeChunk.objects.update_or_create(
        source=source,
        source_id=source_id,
        defaults={
            "salon_id": document.salon_id,
            "title": document.title[:255],
            "content": document.content,
            "content_hash": digest,
            "embedding": vector,
            "embedding_model": embeddings.model,
        },
    )
    return "updated" if existing else "created"


def salon_sources(salon: Salon) -> list[tuple[str, int]]:
    """Every source that belongs to ``salon`` (active or not)."""
    return (
        [(Source.SALON, salon.pk)]
        + [(Source.SERVICE, pk) for pk in salon.services.values_list("pk", flat=True)]
        + [
            (Source.FAQ, pk)
            for pk in FAQ.objects.filter(salon=salon).values_list("pk", flat=True)
        ]
    )


def reindex_salon(salon: Salon) -> dict[str, int]:
    """Re-index every source of a salon and drop orphaned chunks."""
    counts: dict[str, int] = {}
    sources = salon_sources(salon)
    for source, pk in sources:
        outcome = index_source(source, pk)
        counts[outcome] = counts.get(outcome, 0) + 1
    keep = {(s, pk) for s, pk in sources}
    for chunk in KnowledgeChunk.objects.filter(salon=salon):
        if (chunk.source, chunk.source_id) not in keep:
            chunk.delete()
            counts["deleted"] = counts.get("deleted", 0) + 1
    return counts


def search(
    salon: Salon, query: str, *, k: int, max_distance: float
) -> list[ContextItem]:
    """Top-``k`` chunks of **this salon** closest to ``query`` (cosine distance)."""
    embeddings = get_embeddings()
    vector = embeddings.embed_query(query)
    chunks = (
        KnowledgeChunk.objects.filter(salon=salon, embedding_model=embeddings.model)
        .annotate(distance=CosineDistance("embedding", vector))
        .filter(distance__lte=max_distance)
        .order_by("distance")[:k]
    )
    return [
        ContextItem(i, c.source, c.source_id, c.title, c.content)
        for i, c in enumerate(chunks, start=1)
    ]
