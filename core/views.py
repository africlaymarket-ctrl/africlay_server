from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def healthz(request):
    """A minimal readiness check used by the deployment platform.

    A live process without a usable database must not be considered ready: every
    marketplace request ultimately depends on the primary database.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({'status': 'unavailable'}, status=503)

    return JsonResponse({'status': 'ok'})
