"""Backwards-compatible re-export of enums.

Consumer code may import from ``django_opensearch_dsl.management.enums``.
"""

from ..enums import CommandAction as OpensearchAction

__all__ = ["OpensearchAction"]
