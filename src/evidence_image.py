"""증빙서류 칸에 넣을 그림 (물품 대여 사업 수입 쪽 — 연체료 규정 안내문).

기본 그림은 assets/rental_evidence.jpg. 웹에서 다른 그림을 올리면 그것을 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

DEFAULT_RENTAL_EVIDENCE = Path(__file__).resolve().parent.parent / "assets" / "rental_evidence.jpg"
MAX_SIDE_PX = 1600  # 너무 큰 사진은 줄여서 넣는다(문서 용량)


@dataclass(frozen=True)
class EvidenceImage:
    data: bytes
    ext: str  # "jpg" | "png"
    width_px: int
    height_px: int
    label: str = "기본 물품 대여 안내 이미지"


def load_evidence_image(data: bytes, label: str = "올린 이미지") -> EvidenceImage:
    """그림 바이트를 hwpx에 넣을 형태(JPEG/PNG)로. 읽지 못하면 ValueError."""
    from PIL import Image, UnidentifiedImageError

    try:
        im = Image.open(BytesIO(data))
        im.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("증빙 이미지를 읽지 못했습니다. png 또는 jpg 그림 파일을 올려 주세요.") from exc
    fmt = (im.format or "").upper()
    if fmt in ("JPEG", "PNG") and max(im.size) <= MAX_SIDE_PX:
        return EvidenceImage(data, "jpg" if fmt == "JPEG" else "png", im.width, im.height, label)
    im = im.convert("RGB")
    im.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
    buf = BytesIO()
    im.save(buf, "JPEG", quality=88, optimize=True)
    return EvidenceImage(buf.getvalue(), "jpg", im.width, im.height, label)


def default_rental_evidence() -> EvidenceImage | None:
    if not DEFAULT_RENTAL_EVIDENCE.exists():
        return None
    return load_evidence_image(DEFAULT_RENTAL_EVIDENCE.read_bytes(), "기본 물품 대여 안내 이미지")
