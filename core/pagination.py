from rest_framework.pagination import LimitOffsetPagination


class CatalogPagination(LimitOffsetPagination):
    default_limit = 24
    max_limit = 100
