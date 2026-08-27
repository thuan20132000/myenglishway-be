from django.contrib.auth import authenticate, password_validation
from rest_framework import exceptions, serializers
from rest_framework_simplejwt.tokens import RefreshToken

from .models import SELF_ASSIGNABLE_ROLE_CHOICES, Role, User


class UserSerializer(serializers.ModelSerializer):
    """Read representation of a user. Never accepts input."""

    class Meta:
        model = User
        fields = ["id", "email", "full_name", "role", "date_joined"]
        read_only_fields = fields


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    role = serializers.ChoiceField(
        choices=SELF_ASSIGNABLE_ROLE_CHOICES,
        default=Role.STUDENT,
        help_text="Only 'student' or 'creator' may be self-assigned.",
    )

    class Meta:
        model = User
        fields = ["email", "password", "full_name", "role"]

    def validate_email(self, value: str) -> str:
        value = User.objects.normalize_email(value).lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_password(self, value: str) -> str:
        password_validation.validate_password(value)
        return value

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class LoginSerializer(serializers.Serializer):
    """Email/password exchange for a JWT pair.

    Written by hand rather than subclassing simplejwt's TokenObtainPairSerializer
    so the request/response shape is explicit in the schema and the error goes
    through the project's AuthenticationFailed path.
    """

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            username=attrs["email"],
            password=attrs["password"],
        )
        if user is None:
            # 401 rather than a 400 field error: the credentials are well-formed,
            # they just do not identify an active account.
            raise exceptions.AuthenticationFailed(
                "No active account found with the given credentials."
            )
        attrs["user"] = user
        return attrs


class TokenPairSerializer(serializers.Serializer):
    """Response shape for register and login."""

    user = UserSerializer(read_only=True)
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)


class GoogleLoginSerializer(serializers.Serializer):
    """A Google ID token from the browser, plus an optional role for signup."""

    id_token = serializers.CharField(write_only=True)
    role = serializers.ChoiceField(
        choices=SELF_ASSIGNABLE_ROLE_CHOICES,
        default=Role.STUDENT,
        help_text=(
            "Applied only when this sign-in creates the account. Only 'student' "
            "or 'creator' may be self-assigned."
        ),
    )


class GoogleTokenPairSerializer(TokenPairSerializer):
    """Response shape for Google sign-in."""

    created = serializers.BooleanField(
        read_only=True,
        help_text="True when this sign-in created the account.",
    )


class TokenRefreshResponseSerializer(serializers.Serializer):
    """Response shape for token refresh."""

    access = serializers.CharField(read_only=True)


def issue_token_pair(user: User) -> dict:
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}
