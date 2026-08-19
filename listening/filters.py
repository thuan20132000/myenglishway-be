import django_filters as filters

from .models import ExerciseStatus, ListeningExercise


class ListeningExerciseFilterSet(filters.FilterSet):
    status = filters.ChoiceFilter(choices=ExerciseStatus.choices)
    is_published = filters.BooleanFilter()
    owner = filters.NumberFilter(field_name="owner_id")
    language = filters.CharFilter(lookup_expr="iexact")

    class Meta:
        model = ListeningExercise
        fields = ["status", "is_published", "owner", "language"]
