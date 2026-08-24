from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class CloneCheckStatus(str, Enum):
    NOT_STARTED = "not_started"
    NOT_IMPLEMENTED = "not_implemented"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass(frozen=True)
class CloneCheckRequest:
    brand_name: str


@dataclass(frozen=True)
class RedirectResult:
    source_url: str
    status_codes: Tuple[int, ...]
    redirect_chain: Tuple[str, ...]
    final_url: str


@dataclass(frozen=True)
class CloneCandidate:
    url: str
    rank: int
    title: str
    score: int
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class CloneResult:
    main_domain: str
    final_domain: str
    status: str
    status_reason: str


@dataclass(frozen=True)
class SearchResult:
    search_engine: str
    keyword: str
    rank: int
    url: str
    title: str
    redirect: Optional[RedirectResult] = None
    candidate: Optional[CloneCandidate] = None
    clone_result: Optional[CloneResult] = None
    excluded: bool = False


@dataclass(frozen=True)
class CloneCheckResult:
    brand_name: str
    status: CloneCheckStatus
    message: str
    results: Tuple[SearchResult, ...] = ()
