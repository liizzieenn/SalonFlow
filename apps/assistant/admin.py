from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from .models import FAQ, KnowledgeChunk
from .tasks import reindex_salon


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    list_display = ("question", "salon", "is_active", "order")
    list_editable = ("is_active", "order")
    list_filter = ("salon", "is_active")
    search_fields = ("question", "answer")
    list_select_related = ("salon",)


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    """Read-only view of what the assistant knows (derived data)."""

    list_display = ("title", "salon", "source", "embedding_model", "updated_at")
    list_filter = ("salon", "source", "embedding_model")
    search_fields = ("title", "content")
    fields = (
        "salon",
        "source",
        "source_id",
        "title",
        "content",
        "embedding_model",
        "updated_at",
    )
    readonly_fields = fields
    actions = ("rebuild_salon_index",)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    @admin.action(description=_("Rebuild the index of the selected chunks' salons"))
    def rebuild_salon_index(self, request, queryset) -> None:
        salon_ids = set(queryset.values_list("salon_id", flat=True))
        for salon_id in salon_ids:
            reindex_salon.delay(salon_id)
        self.message_user(
            request,
            _("Queued re-indexing for %d salon(s).") % len(salon_ids),
            messages.INFO,
        )
