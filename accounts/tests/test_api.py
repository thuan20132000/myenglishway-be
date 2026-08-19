import pytest
from django.urls import reverse

from accounts.models import Role, User

pytestmark = pytest.mark.django_db

REGISTER_URL = reverse("v1:register")
LOGIN_URL = reverse("v1:login")
REFRESH_URL = reverse("v1:token-refresh")
ME_URL = reverse("v1:me")


def test_register_creates_student_by_default(api_client):
    response = api_client.post(
        REGISTER_URL,
        {"email": "ann@example.com", "password": "s3cret-passphrase", "full_name": "Ann Lee"},
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "ann@example.com"
    assert body["user"]["role"] == Role.STUDENT
    assert body["access"] and body["refresh"]
    assert "password" not in body["user"]


def test_register_as_creator_is_allowed(api_client):
    response = api_client.post(
        REGISTER_URL,
        {"email": "ben@example.com", "password": "s3cret-passphrase", "role": "creator"},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["user"]["role"] == Role.CREATOR


def test_register_as_admin_is_rejected(api_client):
    response = api_client.post(
        REGISTER_URL,
        {"email": "evil@example.com", "password": "s3cret-passphrase", "role": "admin"},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "role" in response.json()
    assert not User.objects.filter(email="evil@example.com").exists()


def test_register_rejects_duplicate_email(api_client, student):
    response = api_client.post(
        REGISTER_URL,
        {"email": student.email, "password": "s3cret-passphrase"},
        format="json",
    )
    assert response.status_code == 400
    assert "email" in response.json()


def test_register_rejects_weak_password(api_client):
    response = api_client.post(
        REGISTER_URL,
        {"email": "weak@example.com", "password": "123"},
        format="json",
    )
    assert response.status_code == 400
    assert "password" in response.json()


def test_login_returns_token_pair(api_client, student):
    response = api_client.post(
        LOGIN_URL,
        {"email": student.email, "password": "test-passphrase-123"},
        format="json",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access"] and body["refresh"]
    assert body["user"]["id"] == student.id


def test_login_with_bad_password_returns_401(api_client, student):
    response = api_client.post(
        LOGIN_URL, {"email": student.email, "password": "wrong"}, format="json"
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_FAILED"


def test_login_for_inactive_user_returns_401(api_client, student):
    student.is_active = False
    student.save()
    response = api_client.post(
        LOGIN_URL,
        {"email": student.email, "password": "test-passphrase-123"},
        format="json",
    )
    assert response.status_code == 401


def test_refresh_returns_new_access_token(api_client, student):
    login = api_client.post(
        LOGIN_URL,
        {"email": student.email, "password": "test-passphrase-123"},
        format="json",
    ).json()
    response = api_client.post(REFRESH_URL, {"refresh": login["refresh"]}, format="json")
    assert response.status_code == 200
    assert response.json()["access"]


def test_refresh_with_invalid_token_returns_401(api_client):
    response = api_client.post(REFRESH_URL, {"refresh": "not-a-token"}, format="json")
    assert response.status_code == 401
    assert response.json()["code"] == "TOKEN_INVALID"


def test_me_requires_authentication(api_client):
    response = api_client.get(ME_URL)
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_FAILED"


def test_me_returns_current_user(api_client, creator):
    login = api_client.post(
        LOGIN_URL,
        {"email": creator.email, "password": "test-passphrase-123"},
        format="json",
    ).json()
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login['access']}")
    response = api_client.get(ME_URL)
    assert response.status_code == 200
    assert response.json() == {
        "id": creator.id,
        "email": creator.email,
        "full_name": creator.full_name,
        "role": Role.CREATOR,
        "date_joined": response.json()["date_joined"],
    }
