"""Camera-local capture dates and canonical garden tags."""
from datetime import date, datetime
import re
import warnings
from PIL import Image


def capture_date(file_storage):
    """Read original/digitized EXIF dates, never modified or upload time."""
    stream = file_storage.stream
    position = stream.tell()
    try:
        stream.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(stream) as image:
                exif = image.getexif()
                nested = exif.get_ifd(0x8769) if exif else {}
                for key in (36867, 36868):
                    value = nested.get(key) or exif.get(key)
                    if isinstance(value, bytes):
                        value = value.decode('ascii', errors='ignore')
                    if isinstance(value, str):
                        try:
                            return datetime.strptime(value.strip().strip('\x00'), '%Y:%m:%d %H:%M:%S').date()
                        except ValueError:
                            continue
    except (OSError, ValueError, TypeError, SyntaxError, KeyError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        pass
    finally:
        stream.seek(position)
    return None


def date_override(value):
    if value in (None, ''):
        return None
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Taken on must be a date (YYYY-MM-DD)')
    return date.fromisoformat(value)


def normalize_tags(values):
    if not isinstance(values, (list, tuple)):
        raise ValueError('Tags must be a list')
    tags = []
    for value in values:
        text = re.sub(r'[^\w\s-]', '', str(value).replace('#', '').strip().lower())
        text = re.sub(r'[\s-]+', '-', text).strip('-')[:64]
        if text and text not in tags:
            tags.append(text)
    if len(tags) > 30:
        raise ValueError('Use at most 30 tags')
    return tags
