import logging
import re
from time import perf_counter

from django.db import connection


logger = logging.getLogger('africlay.performance')


_CALLBACK_TOKEN_PATH = re.compile(
    r'(?P<prefix>/api/payments/(?:provider-callback|mpesa/callback|payouts/provider-(?:result|timeout))/)'
    r'[^/]+/?$'
)


def safe_request_path(path):
    """Keep short-lived payment callback tokens out of application logs."""
    return _CALLBACK_TOKEN_PATH.sub(r'\g<prefix>[redacted]/', path)


class SecurityHeadersMiddleware:
    """Set conservative browser protections that do not alter API authentication."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.setdefault(
            'Permissions-Policy',
            'geolocation=(), camera=(), microphone=(), payment=()',
        )
        response.setdefault('X-Permitted-Cross-Domain-Policies', 'none')
        return response


class RequestTimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = perf_counter()
        db_duration_ms = 0.0
        query_count = 0

        def measure_query(execute, sql, params, many, context):
            nonlocal db_duration_ms, query_count
            query_started = perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                db_duration_ms += (perf_counter() - query_started) * 1000
                query_count += 1

        with connection.execute_wrapper(measure_query):
            response = self.get_response(request)
        duration_ms = (perf_counter() - started) * 1000
        response['Server-Timing'] = (
            f'app;dur={duration_ms:.1f}, '
            f'db;dur={db_duration_ms:.1f};desc="{query_count} queries"'
        )
        logger.info(
            'request method=%s path=%s status=%s duration_ms=%.1f db_duration_ms=%.1f db_queries=%s',
            request.method,
            safe_request_path(request.path),
            response.status_code,
            duration_ms,
            db_duration_ms,
            query_count,
        )
        return response
