from django.core.management.base import BaseCommand

from apps.assistant.knowledge import reindex_salon
from apps.salons.models import Salon


class Command(BaseCommand):
    help = "Embed (or refresh) every salon's services, FAQ and contact info."

    def add_arguments(self, parser) -> None:
        parser.add_argument("slugs", nargs="*", help="Only these salons (default: all)")

    def handle(self, *args, slugs: list[str], **options) -> None:
        salons = Salon.objects.all()
        if slugs:
            salons = salons.filter(slug__in=slugs)
        for salon in salons.order_by("pk"):
            counts = reindex_salon(salon)
            self.stdout.write(f"{salon.slug}: {counts}")
