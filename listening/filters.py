import django_filters as filters

from .models import ExerciseStatus, ListeningExercise


class ListeningExerciseFilterSet(filters.FilterSet):
    status = filters.ChoiceFilter(choices=ExerciseStatus.choices)
    is_published = filters.BooleanFilter()
    owner = filters.NumberFilter(field_name="owner_id")
    language = filters.CharFilter(lookup_expr="iexact")
    collection = filters.NumberFilter(field_name="membership__collection_id")
    #: ``ungrouped=true`` is what the learner's landing page asks for: the
    #: exercises that are not filed under any collection, shown beside the
    #: collections themselves.
    ungrouped = filters.BooleanFilter(field_name="membership", lookup_expr="isnull")

    class Meta:
        model = ListeningExercise
        fields = ["status", "is_published", "owner", "language", "collection", "ungrouped"]
