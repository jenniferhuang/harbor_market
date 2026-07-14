from __future__ import annotations

import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, UnidentifiedImageError

SUPPORTED_IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}


class ProductImageValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ProductImageInspection:
    mime_type: str
    extension: str
    width: int
    height: int


def inspect_product_image(payload: bytes) -> ProductImageInspection:
    """Validate image bytes by signature and bounded decoded dimensions."""
    if not payload:
        raise ProductImageValidationError("invalid_image", "Uploaded image is empty")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as image:
                detected_format = image.format
                width, height = image.size
                image.verify()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ProductImageValidationError(
            "invalid_image_dimensions",
            "Image dimensions are outside safe limits",
        ) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ProductImageValidationError(
            "invalid_image",
            "File is not a valid supported image",
        ) from exc
    if detected_format not in SUPPORTED_IMAGE_FORMATS:
        raise ProductImageValidationError(
            "unsupported_image",
            "Only JPEG, PNG, and WebP images are supported",
        )
    if width < 1 or height < 1 or width > 12_000 or height > 12_000 or width * height > 40_000_000:
        raise ProductImageValidationError(
            "invalid_image_dimensions",
            "Image dimensions are outside safe limits",
        )
    mime_type, extension = SUPPORTED_IMAGE_FORMATS[detected_format]
    return ProductImageInspection(
        mime_type=mime_type,
        extension=extension,
        width=width,
        height=height,
    )
