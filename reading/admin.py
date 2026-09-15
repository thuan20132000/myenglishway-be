"""Django admin for the reading domain.

``word_count`` is derived from the body, so it is not editable here. Sessions
are a learner's personal runs: readable for support, not rewritten.
"""

from django.contrib import admin

from .models import ReadingExercise, ReadingSession


@admin.register(ReadingExercise)
class ReadingExerciseAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "owner",
        "word_count",
        "suggested_wpm",
        "is_published",
        "created_at",
    )
    list_filter = ("is_published", "language")
    search_fields = ("title", "description", "source", "owner__email")
    readonly_fields = ("word_count", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(ReadingSession)
class ReadingSessionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "exercise",
        "mode",
        "target_wpm",
        "actual_wpm",
        "finished_at",
        "created_at",
    )
    list_filter = ("mode",)
    search_fields = ("user__email", "exercise__title")
    readonly_fields = (
        "user",
        "exercise",
        "mode",
        "target_wpm",
        "strict_mode",
        "word_count",
        "started_at",
        "finished_at",
        "paused_ms",
        "reading_ms",
        "progress",
        "actual_wpm",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False
