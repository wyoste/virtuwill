"""Photo capture dates and labels: no live services required."""
import io
import unittest
from datetime import date
from PIL import Image
from werkzeug.datastructures import FileStorage
from virtuwill.photo_metadata import capture_date, date_override, normalize_tags


def photo(original=None, digitized=None, modified=None):
    image = Image.new('RGB', (8, 8), 'green')
    exif = Image.Exif()
    nested = {}
    if original is not None: nested[36867] = original
    if digitized is not None: nested[36868] = digitized
    if nested: exif[0x8769] = nested
    if modified: exif[306] = modified
    stream = io.BytesIO()
    image.save(stream, format='JPEG', exif=exif)
    stream.seek(0)
    return FileStorage(stream=stream, filename='garden.jpg')


class GardenMetadataTests(unittest.TestCase):
    def test_original_wins_and_stream_is_preserved(self):
        f = photo('2024:04:03 23:59:00', '2025:06:07 12:00:00', '2026:09:26 10:00:00')
        f.stream.seek(7)
        self.assertEqual(capture_date(f), date(2024, 4, 3))
        self.assertEqual(f.stream.tell(), 7)

    def test_missing_metadata_never_uses_modified_or_upload_date(self):
        self.assertIsNone(capture_date(photo(modified='2026:09:26 10:00:00')))
        self.assertIsNone(capture_date(FileStorage(stream=io.BytesIO(b'not an image'))))

    def test_digitized_fallback_and_invalid_original(self):
        self.assertEqual(capture_date(photo('bad', '2020:01:02 03:04:05')), date(2020, 1, 2))
        self.assertIsNone(capture_date(photo('0000:00:00 00:00:00')))

    def test_batch_photos_retain_different_dates(self):
        self.assertEqual([capture_date(photo(d)) for d in ['2023:02:01 00:00:00', '2025:09:12 12:00:00']],
                         [date(2023, 2, 1), date(2025, 9, 12)])

    def test_override_is_explicit_and_strict(self):
        self.assertIsNone(date_override(''))
        self.assertEqual(date_override('2024-02-29'), date(2024, 2, 29))
        for bad in ['today', '2025-02-29', 'badTdate', '2026-09-26T00:00:00']:
            with self.assertRaises(ValueError): date_override(bad)

    def test_labels_store_no_hash_and_deduplicate(self):
        self.assertEqual(normalize_tags(['#Spring Blooms', 'spring-blooms', '  ##front-yard  ', '']), ['spring-blooms', 'front-yard'])
        with self.assertRaises(ValueError): normalize_tags('not-a-list')
