import logging

from django.core.exceptions import PermissionDenied
from django.http import Http404


class StripClientErrorTraceback(logging.Filter):
    """Garde l'avertissement « Forbidden: /url » mais retire la trace de pile des 403/404,
    qui sont des situations normales (accès refusé, page absente), pas des bugs."""

    def filter(self, record):
        if record.exc_info and isinstance(record.exc_info[1], (PermissionDenied, Http404)):
            record.exc_info = None
            record.exc_text = None
        return True
