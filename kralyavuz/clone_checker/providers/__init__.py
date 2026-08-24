from typing import List, Protocol

from ..models import SearchResult


class CloneCheckerProvider(Protocol):
    search_engine: str

    def search(self, keyword: str, limit: int = 10) -> List[SearchResult]:
        ...


from .search_provider import SearchProviderError, YandexSearchProvider
from .redirect_provider import RedirectProvider, RedirectProviderError

__all__ = [
    "CloneCheckerProvider",
    "RedirectProvider",
    "RedirectProviderError",
    "SearchProviderError",
    "YandexSearchProvider",
]
