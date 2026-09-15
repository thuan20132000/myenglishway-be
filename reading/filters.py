import django_filters as filters

from .models import ReadingExercise


class ReadingExerciseFilterSet(filters.FilterSet):
    is_published = filters.BooleanFilter()
    owner = filters.NumberFilter(field_name="owner_id")
    language = filters.CharFilter(lookup_expr="iexact")
    #: ``mine=true`` is what the "my passages" tab asks for: the caller's own
    #: uploads, separated from the published catalogue they can also see.
    mine = filters.BooleanFilter(method="filter_mine")

    class Meta:
        model = ReadingExercise
        fields = ["is_published", "owner", "language", "mine"]

    def filter_mine(self, queryset, name, value):
        if value is None:
            return queryset
        user = self.request.user
        return queryset.filter(owner=user) if value else queryset.exclude(owner=user)
