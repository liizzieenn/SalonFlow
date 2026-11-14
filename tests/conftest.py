import pytest
from django.utils import translation

from tests.factories import BookingFactory, SalonFactory, UserFactory


@pytest.fixture(autouse=True)
def english_by_default():
    """Most tests assert English text: activate it so reverse() yields /en/ URLs.

    Georgian behaviour (the site default) is covered explicitly in test_i18n.py.
    """
    with translation.override("en"):
        yield


@pytest.fixture
def user(db):
    return UserFactory()


@pytest.fixture
def salon(db):
    return SalonFactory()


@pytest.fixture
def booking(db):
    return BookingFactory()
