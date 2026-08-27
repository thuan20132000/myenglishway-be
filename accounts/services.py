"""Google sign-in: ID token verification and account resolution.

The browser obtains a Google ID token through Google Identity Services and posts
it here; this module checks it and maps it onto a local ``User``. There is no
authorization-code exchange and no client secret - the API stays stateless and
issues its own JWT pair, exactly as password login does.
"""

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from rest_framework import exceptions

from .models import Role, User

# Google mints tokens under both spellings; anything else is not Google.
GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})

# Deliberately the same wording as LoginSerializer: the client learns that the
# credentials did not identify an active account, and nothing more.
INVALID_CREDENTIALS = "No active account found with the given credentials."


@dataclass(frozen=True)
class GoogleIdentity:
    """The claims we trust from a verified Google ID token."""

    sub: str
    email: str
    full_name: str


def verify_google_id_token(raw_token: str) -> GoogleIdentity:
    """Verify a Google ID token and return the identity it asserts.

    Raises ``AuthenticationFailed`` for anything wrong with the token, and
    ``ImproperlyConfigured`` when the server has no client ID - that is our bug,
    not the caller's, and must not look like a rejected login.
    """
    client_id = settings.GOOGLE_OAUTH_CLIENT_ID
    if not client_id:
        raise ImproperlyConfigured(
            "GOOGLE_OAUTH_CLIENT_ID is not set; Google sign-in cannot be verified."
        )

    try:
        # Checks the signature against Google's public keys, the expiry, and that
        # the token was minted for our client ID.
        claims = google_id_token.verify_oauth2_token(
            raw_token, google_requests.Request(), client_id
        )
    except ValueError as exc:
        raise exceptions.AuthenticationFailed("Invalid Google ID token.") from exc

    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise exceptions.AuthenticationFailed("Invalid Google ID token.")

    email = claims.get("email") or ""
    # Linking by email is only safe because Google vouches for ownership here.
    if not email or not claims.get("email_verified"):
        raise exceptions.AuthenticationFailed("The Google account has no verified email address.")

    return GoogleIdentity(
        sub=claims["sub"],
        email=User.objects.normalize_email(email).lower(),
        full_name=claims.get("name") or "",
    )


def resolve_google_user(identity: GoogleIdentity, role: str = Role.STUDENT) -> tuple[User, bool]:
    """Return the user this identity signs in as, and whether it was just created.

    ``role`` applies to a newly created account only; an existing user keeps the
    role it already has.
    """
    try:
        with transaction.atomic():
            user, created = _link_or_create(identity, role)
    except IntegrityError:
        # A concurrent first sign-in won the unique constraint; its row is now
        # committed, so a second pass finds it.
        with transaction.atomic():
            user, created = _link_or_create(identity, role)

    if not user.is_active:
        raise exceptions.AuthenticationFailed(INVALID_CREDENTIALS)
    return user, created


def _link_or_create(identity: GoogleIdentity, role: str) -> tuple[User, bool]:
    user = User.objects.filter(google_sub=identity.sub).first()
    if user is not None:
        return user, False

    user = User.objects.filter(email__iexact=identity.email).first()
    if user is not None:
        # First Google sign-in for an existing password account: record the sub so
        # every later sign-in matches on it rather than on the email.
        user.google_sub = identity.sub
        fields = ["google_sub"]
        if not user.full_name and identity.full_name:
            user.full_name = identity.full_name
            fields.append("full_name")
        user.save(update_fields=fields)
        return user, False

    # password=None leaves an unusable password, so a Google-only account cannot
    # be signed into with POST /auth/login/ until one is set.
    user = User.objects.create_user(
        email=identity.email,
        password=None,
        full_name=identity.full_name,
        role=role,
        google_sub=identity.sub,
    )
    return user, True
