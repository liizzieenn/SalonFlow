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
    ],
)
def test_admin_pages_render(admin_client_logged_in, model):
    for suffix in ("", "add/"):
        response = admin_client_logged_in.get(f"/admin/{model}/{suffix}")
        assert response.status_code == 200, f"{model}/{suffix}"
