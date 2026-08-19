import django_filters as filters

from .models import AttemptKind, PracticeAttempt


class PracticeAttemptFilterSet(filters.FilterSet):
    exercise = filters.NumberFilter(field_name="exercise_id")
    segment = filters.NumberFilter(field_name="segment_id")
    kind = filters.ChoiceFilter(choices=AttemptKind.choices)
    date_from = filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    date_to = filters.DateFilter(field_name="created_at", lookup_expr="date__lte")

    class Meta:
        model = PracticeAttempt
        fields = ["exercise", "segment", "kind", "date_from", "date_to"]
