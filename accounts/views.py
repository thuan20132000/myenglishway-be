from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView as BaseTokenRefreshView

from common.serializers import ErrorSerializer

from . import services
from .serializers import (
    GoogleLoginSerializer,
    GoogleTokenPairSerializer,
    LoginSerializer,
    RegisterSerializer,
    TokenPairSerializer,
    TokenRefreshResponseSerializer,
    UserSerializer,
    issue_token_pair,
)


@extend_schema(
    tags=["auth"],
    summary="Register a new account",
    description=(
        "Creates an account and returns a JWT pair, so a client can register "
        "and start working without a second round trip.\n\n"
        "`role` may be `student` (the default) or `creator`; `admin` is "
        "rejected and cannot be self-assigned. Passwords are checked against "
        "Django's configured password validators."
    ),
    request=RegisterSerializer,
    responses={201: TokenPairSerializer, 400: ErrorSerializer},
    examples=[
        OpenApiExample(
            "Student registration",
            request_only=True,
            value={
                "email": "ann@example.com",
                "password": "s3cret-passphrase",
                "full_name": "Ann Lee",
                "role": "student",
            },
        )
    ],
    auth=[],
)
class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        body = {"user": UserSerializer(user).data, **issue_token_pair(user)}
        return Response(body, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=["auth"],
    summary="Obtain a JWT pair",
    description=(
        "Exchange email and password for an access and refresh token. Bad "
        "credentials and inactive accounts both return 401 - the response does "
        "not distinguish them."
    ),
    request=LoginSerializer,
    responses={200: TokenPairSerializer, 401: ErrorSerializer},
    auth=[],
)
class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = LoginSerializer

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        body = {"user": UserSerializer(user).data, **issue_token_pair(user)}
        return Response(body, status=status.HTTP_200_OK)


@extend_schema(
    tags=["auth"],
    summary="Sign in with Google",
    description=(
        "Exchange a Google ID token - obtained in the browser through Google "
        "Identity Services with the same client ID the server is configured "
        "with - for an access and refresh token.\n\n"
        "One endpoint covers both signup and login: `created` is true when the "
        "sign-in made the account. An account is matched by Google's `sub` "
        "first, and on the very first sign-in by the token's verified email, so "
        "an existing password account is linked rather than duplicated. Tokens "
        "that are invalid, expired, minted for another client, or carry an "
        "unverified email all return 401."
    ),
    request=GoogleLoginSerializer,
    responses={200: GoogleTokenPairSerializer, 400: ErrorSerializer, 401: ErrorSerializer},
    examples=[
        OpenApiExample(
            "Sign in with a Google ID token",
            request_only=True,
            value={"id_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6IjE2M2M0..."},
        )
    ],
    auth=[],
)
class GoogleLoginView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = GoogleLoginSerializer

    def post(self, request):
        serializer = GoogleLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        identity = services.verify_google_id_token(serializer.validated_data["id_token"])
        user, created = services.resolve_google_user(identity, serializer.validated_data["role"])
        body = {
            "user": UserSerializer(user).data,
            "created": created,
            **issue_token_pair(user),
        }
        return Response(body, status=status.HTTP_200_OK)


@extend_schema(
    tags=["auth"],
    summary="Refresh an access token",
    description=(
        "Exchange a valid refresh token for a new access token. Returns 401 "
        "with `TOKEN_INVALID` if the refresh token is expired or malformed."
    ),
    responses={200: TokenRefreshResponseSerializer, 401: ErrorSerializer},
    auth=[],
)
class TokenRefreshView(BaseTokenRefreshView):
    permission_classes = [permissions.AllowAny]


@extend_schema(
    tags=["auth"],
    summary="Current user profile",
    description="Returns the authenticated user, including their role.",
    responses={200: UserSerializer, 401: ErrorSerializer},
    examples=[
        OpenApiExample(
            "Creator profile",
            response_only=True,
            value={
                "id": 7,
                "email": "ben@example.com",
                "full_name": "Ben Ito",
                "role": "creator",
                "date_joined": "2026-08-19T09:00:00Z",
            },
        )
    ],
)
class MeView(generics.RetrieveAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user
