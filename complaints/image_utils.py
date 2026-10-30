"""Shrinks large photo uploads so complaint pages load faster."""
import io
import os

from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image, ImageOps

MAX_SIDE = 1600       # longest side in pixels
JPEG_QUALITY = 82


def compress_image(uploaded):
    """Return a smaller copy of an uploaded photo, or the original if it cannot be read."""
    if not uploaded:
        return uploaded
    try:
        image = Image.open(uploaded)
        image = ImageOps.exif_transpose(image)  # keep phone photos upright
    except Exception:
        uploaded.seek(0)
        return uploaded

    keep_png = image.mode in ('RGBA', 'LA', 'P')
    image.thumbnail((MAX_SIDE, MAX_SIDE))

    buffer = io.BytesIO()
    base_name = os.path.splitext(os.path.basename(uploaded.name))[0]
    if keep_png:
        image.save(buffer, format='PNG', optimize=True)
        name, content_type = f'{base_name}.png', 'image/png'
    else:
        image.convert('RGB').save(buffer, format='JPEG', quality=JPEG_QUALITY, optimize=True)
        name, content_type = f'{base_name}.jpg', 'image/jpeg'

    size = buffer.tell()
    original_size = getattr(uploaded, 'size', None)
    if original_size and size >= original_size:
        uploaded.seek(0)
        return uploaded  # already small, keep the original

    buffer.seek(0)
    return InMemoryUploadedFile(buffer, 'image', name, content_type, size, None)
