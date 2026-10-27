import pytest

from finance_saber.users.models import User
from finance_saber.users.roles import SUPERUSER, Role, set_role


def make_user(username: str, role: str) -> User:
    user = User.objects.create_user(username=username, password="s3cret-pass")
    set_role(user, role)
    return user


@pytest.fixture
def user(db) -> User:
    """A manager: can use every business page (most tests need that)."""
    return make_user("alice", Role.MANAGER)


@pytest.fixture
def planner(db) -> User:
    return make_user("pavel", Role.PLANNER)


@pytest.fixture
def finance(db) -> User:
    return make_user("farida", Role.FINANCE)


@pytest.fixture
def technologist(db) -> User:
    return make_user("timur", Role.TECHNOLOGIST)


@pytest.fixture
def operator(db) -> User:
    return make_user("olga", Role.OPERATOR)


@pytest.fixture
def superuser(db) -> User:
    return make_user("root", SUPERUSER)


@pytest.fixture
def no_role(db) -> User:
    """Someone who signed up and hasn't been given a role yet."""
    return make_user("newbie", "")
