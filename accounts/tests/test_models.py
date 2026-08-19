import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from accounts.models import Role

User = get_user_model()

pytestmark = pytest.mark.django_db


def test_create_user_defaults_to_student():
    user = User.objects.create_user(email="a@example.com", password="pw-123456789")
    assert user.role == Role.STUDENT
    assert user.is_student
    assert not user.is_creator
    assert not user.is_staff


def test_create_user_normalises_email_domain():
    user = User.objects.create_user(email="a@EXAMPLE.COM", password="pw-123456789")
    assert user.email == "a@example.com"


def test_create_user_requires_email():
    with pytest.raises(ValueError):
        User.objects.create_user(email="", password="pw-123456789")


def test_email_is_unique():
    User.objects.create_user(email="a@example.com", password="pw-123456789")
    with pytest.raises(IntegrityError):
        User.objects.create_user(email="a@example.com", password="pw-123456789")


def test_create_superuser_is_admin_and_staff():
    user = User.objects.create_superuser(email="root@example.com", password="pw-123456789")
    assert user.role == Role.ADMIN
    assert user.is_staff and user.is_superuser
    assert user.is_admin and user.is_creator


def test_admin_role_forces_staff_flag():
    user = User.objects.create_user(
        email="mod@example.com", password="pw-123456789", role=Role.ADMIN
    )
    assert user.is_staff is True


def test_creator_role_grants_authoring_but_not_admin():
    user = User.objects.create_user(
        email="c@example.com", password="pw-123456789", role=Role.CREATOR
    )
    assert user.is_creator
    assert not user.is_admin
    assert not user.is_staff


def test_username_field_is_email():
    assert User.USERNAME_FIELD == "email"
    assert User.REQUIRED_FIELDS == []


def test_self_assignable_roles_exclude_admin():
    assert Role.ADMIN not in Role.self_assignable()
    assert set(Role.self_assignable()) == {Role.STUDENT, Role.CREATOR}
