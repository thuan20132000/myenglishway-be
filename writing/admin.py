"""Django admin for the writing domain.

Read-heavy on purpose. The admin bypasses ``services.py``, and the two things
worth protecting from that are ``page_count`` (derived from the file, so an
edited value silently breaks every page range check) and learner writing, which
is nobody's to edit from here.
"""

from django.contrib import admin

from .models import WritingExercise, WritingNotebook, WritingPage


@admin.register(WritingExercise)
class WritingExerciseAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "page_count", "is_published", "created_at")
    list_filter = ("is_published", "language")
    search_fields = ("title", "description", "source", "owner__email")
    readonly_fields = ("page_count", "created_at", "updated_at")
    ordering = ("-created_at",)


class WritingPageInline(admin.TabularInline):
    model = WritingPage
    extra = 0
    readonly_fields = ("page_number", "body", "word_count", "updated_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(WritingNotebook)
class WritingNotebookAdmin(admin.ModelAdmin):
    list_display = ("user", "exercise", "last_page", "completed_at", "updated_at")
    list_filter = ("completed_at",)
    search_fields = ("user__email", "exercise__title")
    readonly_fields = ("user", "exercise", "created_at", "updated_at")
    inlines = [WritingPageInline]
    ordering = ("-updated_at",)
