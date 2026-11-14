from django.db import connection
from django.http import HttpRequest, JsonResponse


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
