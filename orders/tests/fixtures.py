"""
Shared pytest fixtures for the BI-01 tests, registered once through
`pytest_plugins` in the root conftest.py so that orders/tests/ and the chat
tests see the same names without star-imports (which flake8 flags).
"""

import pytest

from orders.tests import factories


@pytest.fixture
def merchant(db):
    return factories.make_business()


@pytest.fixture
def other_merchant(db):
    return factories.make_business()


@pytest.fixture
def customer(db):
    return factories.make_user("customer")


@pytest.fixture
def other_customer(db):
    return factories.make_user("customer")


@pytest.fixture
def product(merchant):
    return factories.make_product(merchant, price="125.50", currency="EGP")


@pytest.fixture
def other_product(other_merchant):
    return factories.make_product(other_merchant, price="80.00", currency="EGP")


@pytest.fixture
def conversation(merchant, customer):
    return factories.make_conversation(merchant.user, customer)


@pytest.fixture
def product_message(conversation, customer, product):
    return factories.make_product_message(conversation, customer, product)
