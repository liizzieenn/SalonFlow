import pytest

from tests.factories import BookingFactory, SalonFactory, UserFactory


@pytest.fixture
def user(db):
    return UserFactory()


@pytest.fixture
def salon(db):
    return SalonFactory()


@pytest.fixture
def booking(db):
    return BookingFactory()
