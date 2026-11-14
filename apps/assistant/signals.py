"""Queue re-embedding whenever data the assistant knows about changes.

Tasks are queued on commit (the worker must see the new data) and ``robust`` so a
broker outage never breaks saving in the admin.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from apps.salons.models import Salon, Service, Staff, WorkingHours

from .models import FAQ, KnowledgeChunk

Source = KnowledgeChunk.Source


def queue_index(source: str, source_id: int) -> None:
    from .tasks import index_source

    def send() -> None:
        index_source.delay(source, source_id)

    transaction.on_commit(send, robust=True)


@receiver([post_save, post_delete], sender=Service)
def service_changed(sender, instance: Service, **kwargs) -> None:
    queue_index(Source.SERVICE, instance.pk)


@receiver([post_save, post_delete], sender=FAQ)
def faq_changed(sender, instance: FAQ, **kwargs) -> None:
    queue_index(Source.FAQ, instance.pk)


@receiver(post_save, sender=Salon)
def salon_changed(sender, instance: Salon, **kwargs) -> None:
    queue_index(Source.SALON, instance.pk)


@receiver([post_save, post_delete], sender=WorkingHours)
def hours_changed(sender, instance: WorkingHours, **kwargs) -> None:
    queue_index(Source.SALON, instance.staff.salon_id)


@receiver(post_save, sender=Staff)
def staff_changed(sender, instance: Staff, **kwargs) -> None:
    # Name / active flag appear in service texts and opening hours.
    for service_id in instance.services.values_list("pk", flat=True):
        queue_index(Source.SERVICE, service_id)
    queue_index(Source.SALON, instance.salon_id)


@receiver(m2m_changed, sender=Staff.services.through)
def staff_services_changed(sender, instance, action: str, pk_set, **kwargs) -> None:
    if action not in {"post_add", "post_remove", "post_clear"}:
        return
    if isinstance(instance, Staff):
        ids = pk_set or set()
        if action == "post_clear":
            ids = set(
                Service.objects.filter(salon=instance.salon).values_list(
                    "pk", flat=True
                )
            )
        for service_id in ids:
            queue_index(Source.SERVICE, service_id)
    else:  # reverse side: service.staff.add(...)
        queue_index(Source.SERVICE, instance.pk)
