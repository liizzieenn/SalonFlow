import pytest
from django.urls import reverse

from tests.factories import BookingFactory, UserFactory

pytestmark = pytest.mark.django_db


def test_health_endpoint(client):
    response = client.get(reverse("health"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": True}


@pytest.fixture
def admin_client_logged_in(client):
    admin = UserFactory(is_staff=True, is_superuser=True)
    client.force_login(admin)
    BookingFactory()  # populate every changelist with at least one row
    return client


@pytest.mark.parametrize(
    "model",
    [
        "accounts/user",
        "salons/salon",
        "salons/service",
        "salons/staff",
        "clients/client",
        "bookings/booking",
        "notifications/notification",
        "assistant/faq",
        "assistant/knowledgechunk",
    ],
)
def test_admin_pages_render(admin_client_logged_in, model):
    suffixes = ("",) if model in READ_ONLY else ("", "add/")
    for suffix in suffixes:
        response = admin_client_logged_in.get(f"/admin/{model}/{suffix}")
        assert response.status_code == 200, f"{model}/{suffix}"


READ_ONLY = {"notifications/notification", "assistant/knowledgechunk"}


def test_salon_admin_action_queues_assistant_reindex(admin_client_logged_in):
    from unittest import mock

    from apps.salons.models import Salon

    salon = Salon.objects.first()
    with mock.patch("apps.assistant.tasks.reindex_salon.delay") as delay:
        response = admin_client_logged_in.post(
            "/admin/salons/salon/",
            {"action": "rebuild_assistant_index", "_selected_action": [salon.pk]},
        )
    assert response.status_code == 302
    delay.assert_called_once_with(salon.pk)


def test_knowledge_admin_action_queues_reindex(admin_client_logged_in):
    from unittest import mock

    from apps.assistant.knowledge import reindex_salon
    from apps.salons.models import Salon

    salon = Salon.objects.first()
    reindex_salon(salon)
    chunk = salon.knowledge_chunks.first()
    with mock.patch("apps.assistant.tasks.reindex_salon.delay") as delay:
        admin_client_logged_in.post(
            "/admin/assistant/knowledgechunk/",
            {"action": "rebuild_salon_index", "_selected_action": [chunk.pk]},
        )
    delay.assert_called_once_with(salon.pk)
