"""Checks for the optional video or audio evidence attached to a complaint."""
import os

from django.core.exceptions import ValidationError

VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov', '.3gp', '.mkv'}
AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.wav', '.ogg', '.aac', '.amr', '.opus'}
MAX_ATTACHMENT_MB = 25


def media_kind(name):
    ext = os.path.splitext(name or '')[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return 'video'
    if ext in AUDIO_EXTENSIONS:
        return 'audio'
    return ''


def validate_media_attachment(file):
    if not file:
        return
    if not media_kind(getattr(file, 'name', '')):
        allowed = ', '.join(sorted(VIDEO_EXTENSIONS | AUDIO_EXTENSIONS))
        raise ValidationError(f'Please attach a video or audio file ({allowed}).')
    size = getattr(file, 'size', 0) or 0
    if size > MAX_ATTACHMENT_MB * 1024 * 1024:
        raise ValidationError(f'The file is too large. The limit is {MAX_ATTACHMENT_MB} MB.')
