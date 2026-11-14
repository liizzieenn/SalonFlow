import pytest
from django.db import IntegrityError

from tests.factories import ClientFactory

pytestmark = pytest.mark.django_db


def test_client_phone_unique_per_salon():
    client = ClientFactory(phone="+995555000111")
    ClientFactory(phone="+995555000111")  # different salon is fine
    with pytest.raises(IntegrityError):
        ClientFactory(salon=client.salon, phone="+995555000111")


def test_blank_phone_not_unique():
    client = ClientFactory(phone="")
    ClientFactory(salon=client.salon, phone="")


def test_default_language_is_georgian():
    from apps.clients.models import Client

    assert Client().preferred_language == "ka"
