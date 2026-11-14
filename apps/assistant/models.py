"""FAQ entries and the per-salon knowledge index used by the AI assistant."""

from django.db import models
from django.utils.translation import gettext_lazy as _
from pgvector.django import HnswIndex, VectorField

from apps.core.models import SalonScopedModel

#: Must match the embedding provider's output size. Changing it needs a migration
#: and a full re-index (``manage.py reindex_assistant``).
EMBEDDING_DIMENSIONS = 1024


class FAQ(SalonScopedModel):
    """A question/answer pair the salon wants the assistant to know."""

    question = models.CharField(_("question"), max_length=255)
    answer = models.TextField(_("answer"))
    is_active = models.BooleanField(_("active"), default=True)
    order = models.PositiveSmallIntegerField(_("order"), default=0)

    class Meta:
        ordering = ["salon", "order", "question"]
        verbose_name = _("FAQ entry")
        verbose_name_plural = _("FAQ")

    def __str__(self) -> str:
        return self.question


class KnowledgeChunk(models.Model):
    """One embedded piece of salon information (a service, an FAQ, contact info).

    Rows are derived data, rebuilt by Celery whenever the source changes. Every
    search is filtered by ``salon`` so the assistant only ever sees one tenant.
    """

    class Source(models.TextChoices):
        SERVICE = "service", _("Service")
        FAQ = "faq", _("FAQ")
        SALON = "salon", _("Salon info")

    salon = models.ForeignKey(
        "salons.Salon",
        on_delete=models.CASCADE,
        related_name="knowledge_chunks",
        verbose_name=_("salon"),
    )
    source = models.CharField(_("source"), max_length=16, choices=Source.choices)
    source_id = models.PositiveBigIntegerField(_("source id"))
    title = models.CharField(_("title"), max_length=255)
    content = models.TextField(_("content"))
    content_hash = models.CharField(_("content hash"), max_length=64)
    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS)
    embedding_model = models.CharField(_("embedding model"), max_length=64)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        ordering = ["salon", "source", "title"]
        verbose_name = _("knowledge chunk")
        verbose_name_plural = _("knowledge chunks")
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_id"], name="knowledge_chunk_unique_source"
            ),
        ]
        indexes = [
            HnswIndex(
                name="knowledge_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_source_display()}: {self.title}"
