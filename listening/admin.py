"""Django admin for the listening domain.

The admin writes straight to the ORM, so none of the rules the API enforces in
``services.py`` and ``collection_services.py`` apply for free here. Two habits
keep the two paths honest with each other:

* structural rules are re-checked in ``clean()`` by calling the same service
  validators, converted from ``DomainError`` into a form error, and
* state transitions (publish, unpublish) are admin *actions* that call the
  service functions, rather than an editable ``is_published`` checkbox.

Anything the admin can still do that the API cannot - reassigning an owner,
editing a published exercise's status - is deliberate: this is the back door
for support and seeding, and its user is staff.
"""

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html

from common import errors

from .collection_services import (
    assert_can_be_parent,
    assert_can_have_members,
    publish_collection,
    unpublish_collection,
)
from .models import (
    CollectionMembership,
    ExerciseAnswer,
    ExerciseCollection,
    ListeningExercise,
    ListeningNotebook,
    TranscriptSegment,
)
from .services import publish_exercise, unpublish_exercise


def _run(modeladmin, request, queryset, operation, done: str):
    """Apply a service function to each selected row, reporting per-row errors.

    Domain errors are expected here - publishing something already published,
    or not yet ready - so they become messages rather than a 500, and one bad
    row does not abandon the rest of the selection.
    """
    changed = 0
    for obj in queryset:
        try:
            operation(obj)
        except errors.DomainError as exc:
            modeladmin.message_user(request, f"{obj}: {exc.detail}", level=messages.WARNING)
        else:
            changed += 1
    if changed:
        modeladmin.message_user(request, f"{changed} {done}.", level=messages.SUCCESS)


# --------------------------------------------------------------------------
# Exercises
# --------------------------------------------------------------------------


class ExerciseAnswerInline(admin.TabularInline):
    model = ExerciseAnswer
    extra = 0
    fields = ["number", "text"]
    ordering = ["number"]


@admin.register(ListeningExercise)
class ListeningExerciseAdmin(admin.ModelAdmin):
    # Segments are not inlined: a transcribed exercise has hundreds of them,
    # which would make this page unusable. They have their own changelist.
    inlines = [ExerciseAnswerInline]
    list_display = [
        "title",
        "owner",
        "status",
        "is_published",
        "collection_link",
        "duration",
        "segment_count",
        "created_at",
    ]
    list_filter = ["status", "is_published", "language", "owner"]
    search_fields = ["title", "description"]
    ordering = ["-created_at"]
    date_hierarchy = "created_at"
    autocomplete_fields = ["owner"]
    readonly_fields = [
        "created_at",
        "updated_at",
        "published_at",
        "processing_started_at",
        "transcription_provider",
    ]
    actions = ["do_publish", "do_unpublish"]

    fieldsets = (
        (None, {"fields": ("owner", "title", "description", "language")}),
        ("Files", {"fields": ("audio_file", "pdf_file", "duration")}),
        (
            "State",
            {
                "description": (
                    "Publish and unpublish through the changelist actions - they "
                    "run the same precondition checks as the API."
                ),
                "fields": ("status", "is_published", "published_at"),
            },
        ),
        (
            "Transcription",
            {
                "classes": ("collapse",),
                "fields": (
                    "processing_started_at",
                    "processing_error",
                    "transcription_provider",
                ),
            },
        ),
        ("Dates", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("owner", "membership__collection")
            .annotate(_segment_count=Count("segments", distinct=True))
        )

    @admin.display(description="Segments", ordering="_segment_count")
    def segment_count(self, obj) -> int:
        return obj._segment_count

    @admin.display(description="Collection")
    def collection_link(self, obj):
        membership = getattr(obj, "membership", None)
        if membership is None:
            return "—"
        collection = membership.collection
        url = reverse("admin:listening_exercisecollection_change", args=[collection.pk])
        return format_html('<a href="{}">{}</a>', url, collection.title)

    @admin.action(description="Publish the selected exercises")
    def do_publish(self, request, queryset):
        _run(self, request, queryset, publish_exercise, "published")

    @admin.action(description="Unpublish the selected exercises")
    def do_unpublish(self, request, queryset):
        _run(self, request, queryset, unpublish_exercise, "unpublished")


@admin.register(TranscriptSegment)
class TranscriptSegmentAdmin(admin.ModelAdmin):
    list_display = ["exercise", "sequence", "start_time", "end_time", "word_count", "text"]
    list_filter = ["exercise__owner", "exercise__status"]
    search_fields = ["text", "exercise__title"]
    ordering = ["exercise", "sequence"]
    autocomplete_fields = ["exercise"]
    readonly_fields = ["word_count", "created_at", "updated_at"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("exercise")


@admin.register(ExerciseAnswer)
class ExerciseAnswerAdmin(admin.ModelAdmin):
    list_display = ["exercise", "number", "text"]
    list_filter = ["exercise__owner"]
    search_fields = ["text", "exercise__title"]
    ordering = ["exercise", "number"]
    autocomplete_fields = ["exercise"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("exercise")


# --------------------------------------------------------------------------
# Collections
# --------------------------------------------------------------------------


class CollectionMembershipInlineForm(forms.ModelForm):
    """One row of the exercises inline.

    The per-row rules live in ``clean_exercise`` rather than in the formset so
    they run *before* Django's model-uniqueness check on the one-to-one, whose
    "Collection membership with this Exercise already exists" names the join
    table instead of the collection the staff user has to go and fix.
    """

    #: The parent collection, injected by the formset below.
    collection_instance = None

    class Meta:
        model = CollectionMembership
        fields = ["position", "exercise"]

    def clean_exercise(self):
        exercise = self.cleaned_data["exercise"]
        collection = self.collection_instance
        if collection is None or collection.pk is None:
            return exercise

        if exercise.owner_id != collection.owner_id:
            raise ValidationError(
                f"“{exercise.title}” belongs to {exercise.owner}, "
                f"not to {collection.owner}."
            )

        membership = (
            CollectionMembership.objects.select_related("collection")
            .filter(exercise=exercise)
            .exclude(collection_id=collection.pk)
            .first()
        )
        if membership is not None:
            raise ValidationError(
                f"“{exercise.title}” is already in "
                f"“{membership.collection.title}”. Remove it there first."
            )
        return exercise


class CollectionMembershipInlineFormSet(forms.BaseInlineFormSet):
    """Re-applies the leaf rule the service layer enforces.

    Without this the admin would happily file exercises into a book, leaving
    data the API considers impossible.
    """

    def _construct_form(self, i, **kwargs):
        form = super()._construct_form(i, **kwargs)
        form.collection_instance = self.instance
        return form

    def clean(self):
        super().clean()
        collection = self.instance
        if collection.pk is None:
            return

        keeps_rows = any(
            form.cleaned_data
            and not form.cleaned_data.get("DELETE")
            and form.cleaned_data.get("exercise") is not None
            for form in self.forms
        )
        if not keeps_rows:
            return

        try:
            assert_can_have_members(collection)
        except errors.DomainError as exc:
            raise ValidationError(str(exc.detail)) from exc


class CollectionMembershipInline(admin.TabularInline):
    model = CollectionMembership
    form = CollectionMembershipInlineForm
    formset = CollectionMembershipInlineFormSet
    extra = 0
    fields = ["position", "exercise"]
    ordering = ["position"]
    autocomplete_fields = ["exercise"]
    verbose_name_plural = "Exercises in this collection"


class ExerciseCollectionForm(forms.ModelForm):
    class Meta:
        model = ExerciseCollection
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        parent = cleaned.get("parent")
        owner = cleaned.get("owner")
        if parent is None or owner is None:
            return cleaned

        try:
            assert_can_be_parent(parent, owner_id=owner.pk, child_id=self.instance.pk)
        except errors.DomainError as exc:
            raise ValidationError({"parent": str(exc.detail)}) from exc

        # Mirrors update_collection: moving a collection that has children of
        # its own would put those children on a third level.
        if self.instance.pk and self.instance.children.exists():
            raise ValidationError(
                {
                    "parent": (
                        "This collection has collections inside it, so it cannot "
                        "itself go inside another."
                    )
                }
            )
        return cleaned


@admin.register(ExerciseCollection)
class ExerciseCollectionAdmin(admin.ModelAdmin):
    form = ExerciseCollectionForm
    inlines = [CollectionMembershipInline]
    list_display = [
        "title",
        "owner",
        "parent",
        "position",
        "is_published",
        "child_count",
        "member_count",
        "created_at",
    ]
    list_filter = ["is_published", "owner"]
    search_fields = ["title", "description"]
    ordering = ["parent__id", "position"]
    # "parent" is deliberately not an autocomplete: that widget resolves through
    # the related admin's search view and would ignore the roots-only queryset
    # in formfield_for_foreignkey below.
    autocomplete_fields = ["owner"]
    readonly_fields = ["published_at", "created_at", "updated_at"]
    actions = ["do_publish", "do_unpublish"]

    fieldsets = (
        (None, {"fields": ("owner", "title", "description")}),
        (
            "Placement",
            {
                "description": (
                    "Leave “parent” empty for a book or a flat category. A "
                    "collection inside another cannot hold further collections, "
                    "only exercises."
                ),
                "fields": ("parent", "position"),
            },
        ),
        (
            "State",
            {
                "description": (
                    "Publish and unpublish through the changelist actions. "
                    "Publication does not cascade: the exercises inside keep "
                    "whatever state they already had."
                ),
                "fields": ("is_published", "published_at"),
            },
        ),
        ("Dates", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("owner", "parent")
            .annotate(
                _child_count=Count("children", distinct=True),
                _member_count=Count("memberships", distinct=True),
            )
        )

    @admin.display(description="Collections inside", ordering="_child_count")
    def child_count(self, obj) -> int:
        return obj._child_count

    @admin.display(description="Exercises", ordering="_member_count")
    def member_count(self, obj) -> int:
        return obj._member_count

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Only a root can be a parent, so children are not worth offering.
        if db_field.name == "parent":
            kwargs["queryset"] = ExerciseCollection.objects.filter(parent__isnull=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.action(description="Publish the selected collections")
    def do_publish(self, request, queryset):
        _run(self, request, queryset, publish_collection, "published")

    @admin.action(description="Unpublish the selected collections")
    def do_unpublish(self, request, queryset):
        _run(self, request, queryset, unpublish_collection, "unpublished")


@admin.register(ListeningNotebook)
class ListeningNotebookAdmin(admin.ModelAdmin):
    """Metadata only — the body is never shown, even to staff."""

    list_display = ("user", "exercise", "word_count", "completed_at", "updated_at")
    list_filter = ("completed_at",)
    search_fields = ("user__email", "exercise__title")
    readonly_fields = (
        "user",
        "exercise",
        "word_count",
        "completed_at",
        "created_at",
        "updated_at",
    )
    exclude = ("body",)
    ordering = ("-updated_at",)

    def has_add_permission(self, request):
        return False


admin.site.site_header = "IELTS listening practice"
admin.site.site_title = "Listening practice admin"
admin.site.index_title = "Content and accounts"
