"""hwpx → hwp 일괄 변환 (Windows + 한글 COM, pyhwpx)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


class HwpConversionError(RuntimeError):
    pass


def hwp_conversion_available() -> bool:
    """Windows + pyhwpx + 한글 환경에서만 True."""
    return sys.platform == "win32"


def _import_hwp_class():
    if not hwp_conversion_available():
        raise HwpConversionError(
            "이 서버는 Linux 클라우드 환경입니다. hwp 변환은 지원하지 않습니다. "
            "hwpx 양식을 업로드하고, 결과 hwpx를 한글에서 hwp로 저장해 주세요."
        )
    try:
        from pyhwpx import Hwp

        return Hwp
    except ImportError as exc:
        msg = "hwp 변환에 pyhwpx가 필요합니다: pip install pyhwpx"
        err = str(exc).lower()
        if "numpy" in err or "multiarray" in err or "pandas" in err:
            msg = (
                "pyhwpx 의존성(numpy/pandas) 버전이 맞지 않습니다. "
                '터미널에서 pip install "numpy<2" 실행 후 서버를 재시작하세요.'
            )
        raise HwpConversionError(msg) from exc


def convert_hwpx_to_hwp(
    hwpx_paths: list[str],
    *,
    visible: bool = False,
) -> list[str]:
    """
    채워진 .hwpx 파일을 같은 경로·이름의 .hwp로 저장한다.

    한글(아래아한글)이 설치되어 있어야 하며 pyhwpx가 필요하다.
    """
    if not hwpx_paths:
        return []

    Hwp = _import_hwp_class()

    written: list[str] = []
    hwp = Hwp(visible=visible)
    try:
        if hasattr(hwp, "SetMessageBoxMode"):
            hwp.SetMessageBoxMode(0x00010000)

        for hwpx_path in hwpx_paths:
            src = os.path.abspath(hwpx_path)
            dst = str(Path(src).with_suffix(".hwp"))
            hwp.open(src)
            if not hwp.save_as(dst):
                raise HwpConversionError(f"hwp 저장 실패: {dst}")
            if Path(dst).exists():
                written.append(dst)
            if hasattr(hwp, "clear"):
                hwp.clear()
    except Exception as exc:
        if not isinstance(exc, HwpConversionError):
            raise HwpConversionError(
                "한글이 설치되어 있는지 확인하세요. "
                f"변환 중 오류: {exc}"
            ) from exc
        raise
    finally:
        hwp.quit()

    return written


def convert_hwp_to_hwpx(
    hwp_path: str,
    hwpx_path: str | None = None,
    *,
    visible: bool = False,
) -> str:
    """
    .hwp 양식을 .hwpx로 변환한다. 한글+pyhwpx 필요.
    hwpx_path 생략 시 같은 경로에 .hwpx 확장자로 저장.
    """
    src = os.path.abspath(hwp_path)
    dst = hwpx_path or str(Path(src).with_suffix(".hwpx"))

    Hwp = _import_hwp_class()

    hwp = Hwp(visible=visible)
    try:
        if hasattr(hwp, "SetMessageBoxMode"):
            hwp.SetMessageBoxMode(0x00010000)
        hwp.open(src)
        if not hwp.save_as(dst):
            raise HwpConversionError(f"hwpx 저장 실패: {dst}")
        if not Path(dst).exists():
            raise HwpConversionError(f"hwpx 파일이 생성되지 않았습니다: {dst}")
        return dst
    except Exception as exc:
        if not isinstance(exc, HwpConversionError):
            raise HwpConversionError(
                "한글이 설치되어 있는지 확인하세요. "
                f"변환 중 오류: {exc}"
            ) from exc
        raise
    finally:
        hwp.quit()
