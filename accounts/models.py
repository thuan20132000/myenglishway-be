from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models


class Role(models.TextChoices):
    STUDENT = "student", "Student"
    CREATOR = "creator", "Creator"
    ADMIN = "admin", "Admin"

    @classmethod
    def self_assignable(cls) -> list[str]:
        """Roles a user may pick for themselves at registration."""
        return [cls.STUDENT, cls.CREATOR]


# The one choice set for roles a user may pick for themselves, shared by every
# serializer that accepts a role so the API schema names it once.
SELF_ASSIGNABLE_ROLE_CHOICES = [(r, r.label) for r in Role.self_assignable()]


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.STUDENT)
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Application user, identified by email.

    ``role`` is a single indexed column rather than a Group membership: there
    are exactly three fixed, mutually exclusive personas, they are read on
    nearly every request by permission classes, and a column keeps that check
    join-free.

    ``google_sub`` is Google's immutable subject id, null for password-only
    accounts. Google sign-in matches on it first and falls back to a
    *verified* Google email only the first time, because a Google account's
    email can change while its ``sub`` cannot.

    PermissionsMixin is retained so Django Groups can be layered on later for
    finer-grained or per-object rules without touching ``role``.
    """

    email = models.EmailField(unique=True, db_index=True)
    full_name = models.CharField(max_length=150, blank=True)
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.STUDENT,
        db_index=True,
    )
    google_sub = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text="Google's stable subject identifier, set on first Google sign-in.",
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        ordering = ["-date_joined"]

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        # Admin role implies Django admin access; keep the two from drifting.
        if self.role == Role.ADMIN:
            self.is_staff = True
        super().save(*args, **kwargs)

    @property
    def is_student(self) -> bool:
        return self.role == Role.STUDENT

    @property
    def is_creator(self) -> bool:
        """True for anyone who may author exercises."""
        return self.role in (Role.CREATOR, Role.ADMIN)

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN
