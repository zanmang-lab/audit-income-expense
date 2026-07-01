"""웹/API용 오류 메시지 포맷."""
from __future__ import annotations

import traceback
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")


class PipelineError(Exception):
    """파이프라인 단계에서 발생한 오류."""

    def __init__(self, stage: str, cause: BaseException):
        self.stage = stage
        self.cause = cause
        super().__init__(format_error_detail(stage, cause))


def format_error_detail(stage: str, exc: BaseException) -> str:
    """사용자에게 보여줄 오류 문구: 단계 + 예외 + 발생 위치."""
    location = _exception_location(exc)
    message = str(exc).strip() or repr(exc)
    if location:
        return f"[{stage}] {type(exc).__name__}: {message} (위치: {location})"
    return f"[{stage}] {type(exc).__name__}: {message}"


def _exception_location(exc: BaseException) -> str:
    tb = traceback.extract_tb(exc.__traceback__)
    if not tb:
        return ""
    frame = tb[-1]
    filename = Path(frame.filename).name
    return f"{filename}:{frame.lineno}, {frame.name}"


def run_stage(stage: str, func: Callable[[], T]) -> T:
    try:
        return func()
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(stage, exc) from exc
