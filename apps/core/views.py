from urllib.parse import urlsplit

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import translation
from django.utils.translation import get_language_from_path
from django.views.decorators.http import require_POST
from django.views.i18n import set_language as django_set_language


def health(request: HttpRequest) -> JsonResponse:
    """Liveness/readiness probe used by Docker and the deploy pipeline."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        db_ok = True
    except Exception:  # pragma: no cover - only hit when the DB is down
        db_ok = False
    status = 200 if db_ok else 503
    return JsonResponse(
        {"status": "ok" if db_ok else "error", "db": db_ok}, status=status
    )


@require_POST
def set_language(request: HttpRequest) -> HttpResponse:
    """Django's ``set_language``, resolving ``next`` in *its own* language.

    ``translate_url`` resolves the URL with the active language. On the
    unprefixed ``/i18n/setlang/`` endpoint that is the cookie/browser language,
    so switching from an ``/en/…`` page to Georgian would fail to resolve and
    leave the visitor on the English page. Activating the language of ``next``
    (from its prefix; unprefixed means the default) makes it reliable.
    """
    next_url = request.POST.get("next", "")
    language = get_language_from_path(urlsplit(next_url).path) or settings.LANGUAGE_CODE
    with translation.override(language):
        return django_set_language(request)
