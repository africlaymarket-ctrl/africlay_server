from io import BytesIO
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError


def _source_bytes(upload):
    try:
        upload.seek(0)
    except (AttributeError, OSError):
        pass
    data = upload.read()
    try:
        upload.seek(0)
    except (AttributeError, OSError):
        pass
    return data


def optimized_webp(source, *, max_dimension, quality=82, filename=None):
    """Return a resized WebP while preserving transparency when present."""
    source_bytes = source if isinstance(source, bytes) else _source_bytes(source)
    try:
        with Image.open(BytesIO(source_bytes)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            has_alpha = image.mode in {'RGBA', 'LA'} or (
                image.mode == 'P' and 'transparency' in image.info
            )
            image = image.convert('RGBA' if has_alpha else 'RGB')
            output = BytesIO()
            image.save(output, format='WEBP', quality=quality, method=6, optimize=True)
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as error:
        raise ValidationError('Upload a valid JPG, PNG, GIF, or WebP image.') from error

    name = filename or f'{uuid4().hex}.webp'
    if not name.lower().endswith('.webp'):
        name = f'{name.rsplit(".", 1)[0]}.webp'
    return ContentFile(output.getvalue(), name=name)


def marketplace_image_variants(upload):
    source = _source_bytes(upload)
    identifier = uuid4().hex
    return (
        optimized_webp(source, max_dimension=1600, quality=82, filename=f'{identifier}-detail.webp'),
        optimized_webp(source, max_dimension=640, quality=78, filename=f'{identifier}-thumb.webp'),
    )
