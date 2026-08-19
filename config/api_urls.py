"""Version 1 URL surface.

Everything the API exposes is aggregated here and mounted under ``/api/v1/``
with the ``v1`` namespace, so DRF's NamespaceVersioning can resolve
``request.version``. A future v2 adds a sibling module rather than editing this
one.
"""

from django.urls import include, path

app_name = "v1"

urlpatterns = [
    path("auth/", include("accounts.urls")),
    path("listening/", include("listening.urls")),
    path("practice/", include("practice.urls")),
]
