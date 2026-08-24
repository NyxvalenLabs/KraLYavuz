from .btk_operator import BtkOperatorResult
from .opera_btk_runner import run_private_check


BtkResult = BtkOperatorResult


def check_btk(value: str) -> BtkOperatorResult:
    return run_private_check(value)
