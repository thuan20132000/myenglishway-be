import pytest
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse
from rest_framework import exceptions

from accounts import services
from accounts.models import Role, User
from accounts.services import GoogleIdentity

pytestmark = pytest.mark.django_db

GOOGLE_URL = reverse("v1:google-login")
LOGIN_URL = reverse("v1:login")
ME_URL = reverse("v1:me")


@pytest.fixture
def google_identity(monkeypatch):
    """Swap token verification for a stub, so no token or network call is needed.

    Returns a setter; the view resolves the function through the module, so
    patching the module attribute is enough.
    """

    def _stub(sub="google-sub-1", email="ann@example.com", full_name="Ann Lee"):
        identity = GoogleIdentity(sub=sub, email=email, full_name=full_name)
        monkeypatch.setattr(services, "verify_google_id_token", lambda raw: identity)
        return identity

    return _stub


def test_first_sign_in_creates_a_student(api_client, google_identity):
    google_identity()
    response = api_client.post(GOOGLE_URL, {"id_token": "stub"}, format="json")

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["user"]["email"] == "ann@example.com"
    assert body["user"]["full_name"] == "Ann Lee"
    assert body["user"]["role"] == Role.STUDENT

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access']}")
    assert api_client.get(ME_URL).json()["id"] == body["user"]["id"]


def test_new_account_has_no_usable_password(api_client, google_identity):
    google_identity()
    api_client.post(GOOGLE_URL, {"id_token": "stub"}, format="json")

    user = User.objects.get(email="ann@example.com")
    assert not user.has_usable_password()
    assert user.google_sub == "google-sub-1"


def test_role_creator_is_honoured_on_first_sign_in(api_client, google_identity):
    google_identity()
    response = api_client.post(GOOGLE_URL, {"id_token": "stub", "role": "creator"}, format="json")
    assert response.status_code == 200
    assert response.json()["user"]["role"] == Role.CREATOR


def test_role_admin_is_rejected(api_client, google_identity):
    google_identity()
    response = api_client.post(GOOGLE_URL, {"id_token": "stub", "role": "admin"}, format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert not User.objects.filter(email="ann@example.com").exists()


def test_second_sign_in_reuses_the_account(api_client, google_identity):
    google_identity()
    first = api_client.post(GOOGLE_URL, {"id_token": "stub"}, format="json").json()
    second = api_client.post(GOOGLE_URL, {"id_token": "stub"}, format="json").json()

    assert second["created"] is False
    assert second["user"]["id"] == first["user"]["id"]
    assert User.objects.filter(email="ann@example.com").count() == 1


def test_role_is_ignored_for_an_existing_account(api_client, google_identity):
    google_identity()
    api_client.post(GOOGLE_URL, {"id_token": "stub"}, format="json")
    response = api_client.post(GOOGLE_URL, {"id_token": "stub", "role": "creator"}, format="json")
    assert response.json()["user"]["role"] == Role.STUDENT


def test_existing_password_account_is_linked_not_duplicated(api_client, student, google_identity):
    google_identity(email=student.email.upper())
    response = api_client.post(GOOGLE_URL, {"id_token": "stub"}, format="json")

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is False
    assert body["user"]["id"] == student.id

    student.refresh_from_db()
    assert student.google_sub == "google-sub-1"
    # Linking must not cost them their password login.
    password_login = api_client.post(
        LOGIN_URL,
        {"email": student.email, "password": "test-passphrase-123"},
        format="json",
    )
    assert password_login.status_code == 200


def test_changed_google_email_still_matches_on_sub(api_client, google_identity):
    google_identity()
    first = api_client.post(GOOGLE_URL, {"id_token": "stub"}, format="json").json()

    google_identity(email="ann.lee@example.com")
    second = api_client.post(GOOGLE_URL, {"id_token": "stub"}, format="json").json()

    assert second["user"]["id"] == first["user"]["id"]
    assert second["created"] is False
    assert User.objects.count() == 1


def test_inactive_user_is_rejected(api_client, student, google_identity):
    student.is_active = False
    student.save()
    google_identity(email=student.email)

    response = api_client.post(GOOGLE_URL, {"id_token": "stub"}, format="json")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_FAILED"


def test_invalid_token_returns_401(api_client, monkeypatch):
    def _reject(raw):
        raise exceptions.AuthenticationFailed("Invalid Google ID token.")

    monkeypatch.setattr(services, "verify_google_id_token", _reject)
    response = api_client.post(GOOGLE_URL, {"id_token": "nope"}, format="json")

    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_FAILED"
    assert not User.objects.exists()


def test_missing_id_token_returns_400(api_client):
    response = api_client.post(GOOGLE_URL, {}, format="json")
    assert response.status_code == 400
    assert "id_token" in response.json()


def test_unverified_email_is_rejected(settings, monkeypatch):
    settings.GOOGLE_OAUTH_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
    monkeypatch.setattr(
        services.google_id_token,
        "verify_oauth2_token",
        lambda *args, **kwargs: {
            "iss": "https://accounts.google.com",
            "sub": "google-sub-1",
            "email": "ann@example.com",
            "email_verified": False,
        },
    )
    with pytest.raises(exceptions.AuthenticationFailed):
        services.verify_google_id_token("stub")
    assert not User.objects.exists()


def test_foreign_issuer_is_rejected(settings, monkeypatch):
    settings.GOOGLE_OAUTH_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
    monkeypatch.setattr(
        services.google_id_token,
        "verify_oauth2_token",
        lambda *args, **kwargs: {
            "iss": "https://evil.example.com",
            "sub": "google-sub-1",
            "email": "ann@example.com",
            "email_verified": True,
        },
    )
    with pytest.raises(exceptions.AuthenticationFailed):
        services.verify_google_id_token("stub")


def test_missing_client_id_is_a_configuration_error(settings):
    settings.GOOGLE_OAUTH_CLIENT_ID = ""
    with pytest.raises(ImproperlyConfigured):
        services.verify_google_id_token("stub")
