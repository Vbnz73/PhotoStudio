import struct
import tempfile
import unittest
import zlib
from decimal import localcontext
from pathlib import Path
from unittest.mock import patch

import photo_export
from photo_export import (
    MAX_DPI,
    MAX_SIDE_PX,
    MIN_DPI,
    calculate_export_size,
    dpi_to_dots_per_meter,
    get_image_dpi,
    prepare_export_image,
    set_image_dpi,
)

try:
    from PySide6.QtGui import QColor, QImage, QImageReader, QImageWriter
except ImportError as error:
    QT_IMPORT_ERROR = f"{type(error).__name__}: {error}"
    QColor = QImage = QImageReader = QImageWriter = None
else:
    QT_IMPORT_ERROR = None


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def read_png_phys(data):
    """Прочитать настоящий pHYs; потенциально конфликтующие EXIF/XMP не скрывать."""
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("Не PNG")
    position = len(PNG_SIGNATURE)
    density = None
    seen_image_data = False
    while position + 12 <= len(data):
        length = struct.unpack_from(">I", data, position)[0]
        chunk_type = data[position + 4:position + 8]
        chunk_end = position + 12 + length
        if chunk_end > len(data):
            raise ValueError("Обрезанный блок PNG")
        payload = data[position + 8:position + 8 + length]
        stored_crc = struct.unpack_from(">I", data, position + 8 + length)[0]
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != stored_crc:
            raise ValueError("Некорректная контрольная сумма PNG")
        if chunk_type == b"pHYs":
            if length != 9 or density is not None or seen_image_data:
                raise ValueError("Некорректный или повторный блок pHYs")
            density = struct.unpack(">IIB", payload)
        elif chunk_type == b"IDAT":
            seen_image_data = True
        elif (chunk_type == b"eXIf" or
              (chunk_type in (b"iTXt", b"zTXt", b"tEXt") and
               payload.split(b"\x00", 1)[0].lower() in
               (b"xml:com.adobe.xmp", b"raw profile type exif"))):
            raise ValueError("PNG содержит EXIF/XMP; проверьте его DPI отдельно")
        if chunk_type == b"IEND":
            if density is None:
                raise ValueError("В PNG отсутствуют физические метаданные pHYs")
            return density
        position = chunk_end
    raise ValueError("Обрезанный PNG или отсутствует IEND")


def read_jpeg_jfif(data):
    """Вернуть единицу, X/Y JFIF и признак наличия EXIF/XMP до начала данных."""
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("Не JPEG")
    position = 2
    density = None
    has_extra_metadata = False
    while position < len(data):
        if data[position] != 0xFF:
            raise ValueError("Некорректный маркер JPEG")
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            raise ValueError("Обрезанный маркер JPEG")
        marker = data[position]
        position += 1
        if marker in (0xD9, 0xDA):  # EOI или SOS
            break
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            continue
        if position + 2 > len(data):
            raise ValueError("Обрезанная длина сегмента JPEG")
        length = struct.unpack_from(">H", data, position)[0]
        if length < 2 or position + length > len(data):
            raise ValueError("Обрезанный сегмент JPEG")
        payload = data[position + 2:position + length]
        if marker == 0xE0 and payload.startswith(b"JFIF\x00"):
            if len(payload) < 12 or density is not None:
                raise ValueError("Некорректные или повторные метаданные JFIF")
            unit = payload[7]
            x_density, y_density = struct.unpack_from(">HH", payload, 8)
            if unit not in (1, 2) or not x_density or not y_density:
                raise ValueError("В JFIF не задана физическая плотность")
            density = (unit, x_density, y_density)
        elif marker == 0xE1 and payload.startswith(
                (b"Exif\x00\x00", b"http://ns.adobe.com/xap/1.0/\x00")):
            has_extra_metadata = True
        position += length
    if density is None:
        raise ValueError("В JPEG отсутствует физическая плотность JFIF")
    return (*density, has_extra_metadata)


def png_chunk(chunk_type, payload):
    crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return (struct.pack(">I", len(payload)) + chunk_type + payload +
            struct.pack(">I", crc))


def sample_jpeg_jfif(unit, x_density, y_density, exif=False, xmp=False):
    payload = (b"JFIF\x00\x01\x02" + bytes([unit]) +
               struct.pack(">HHBB", x_density, y_density, 0, 0))
    result = b"\xff\xd8\xff\xe0" + struct.pack(">H", len(payload) + 2) + payload
    if exif:
        exif_payload = b"Exif\x00\x00"
        result += b"\xff\xe1" + struct.pack(">H", len(exif_payload) + 2) + exif_payload
    if xmp:
        xmp_payload = b"http://ns.adobe.com/xap/1.0/\x00"
        result += b"\xff\xe1" + struct.pack(">H", len(xmp_payload) + 2) + xmp_payload
    return result + b"\xff\xd9"


class MetadataParserTests(unittest.TestCase):
    """Проверяют только анализ байтов, а не работу кодеков или Qt."""

    def test_png_phys_present(self):
        data = (PNG_SIGNATURE + png_chunk(b"pHYs", struct.pack(">IIB", 11811, 11811, 1)) +
                png_chunk(b"IEND", b""))
        self.assertEqual(read_png_phys(data), (11811, 11811, 1))

    def test_png_missing_phys_and_bad_crc(self):
        with self.assertRaises(ValueError):
            read_png_phys(PNG_SIGNATURE + png_chunk(b"IEND", b""))
        damaged = bytearray(PNG_SIGNATURE + png_chunk(b"pHYs", struct.pack(">IIB", 1, 1, 1)))
        damaged[-1] ^= 1
        damaged.extend(png_chunk(b"IEND", b""))
        with self.assertRaises(ValueError):
            read_png_phys(bytes(damaged))

    def test_png_rejects_competing_or_duplicate_metadata(self):
        phys = png_chunk(b"pHYs", struct.pack(">IIB", 11811, 11811, 1))
        competing = (png_chunk(b"eXIf", b"example"),
                     png_chunk(b"iTXt", b"XML:com.adobe.xmp\x00example"),
                     png_chunk(b"tEXt", b"Raw profile type exif\x00example"), phys)
        for chunk in competing:
            with self.subTest(chunk=chunk):
                data = PNG_SIGNATURE + phys + chunk + png_chunk(b"IEND", b"")
                with self.assertRaises(ValueError):
                    read_png_phys(data)

    def test_jpeg_jfif_inches_and_centimeters(self):
        self.assertEqual(read_jpeg_jfif(sample_jpeg_jfif(1, 300, 300)),
                         (1, 300, 300, False))
        self.assertEqual(read_jpeg_jfif(sample_jpeg_jfif(2, 50, 50)),
                         (2, 50, 50, False))
        self.assertEqual(read_jpeg_jfif(sample_jpeg_jfif(1, 300, 300, exif=True)),
                         (1, 300, 300, True))
        self.assertEqual(read_jpeg_jfif(sample_jpeg_jfif(1, 300, 300, xmp=True)),
                         (1, 300, 300, True))

    def test_jpeg_without_physical_density_is_rejected(self):
        for data in (b"not a jpeg", b"\xff\xd8\xff\xd9",
                     sample_jpeg_jfif(0, 1, 1), sample_jpeg_jfif(1, 0, 300)):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    read_jpeg_jfif(data)


@unittest.skipIf(QT_IMPORT_ERROR is not None,
                 f"PySide6/QtGui недоступен: {QT_IMPORT_ERROR}")
class QImageDpiTests(unittest.TestCase):
    def make_image(self, width=4, height=3):
        image = QImage(width, height, QImage.Format.Format_RGB32)
        self.assertFalse(image.isNull(), "Не удалось создать тестовый QImage")
        image.fill(QColor("#123456"))
        return image

    def write_and_read(self, path, fmt, image):
        available = {bytes(item).lower() for item in QImageWriter.supportedImageFormats()}
        aliases = {b"jpeg", b"jpg"} if fmt == b"JPEG" else {b"png"}
        self.assertTrue(available & aliases, f"Отсутствует кодек записи {fmt!r}")
        writer = QImageWriter(str(path), fmt)
        self.assertTrue(writer.write(image), writer.errorString())
        reader = QImageReader(str(path))
        decoded = reader.read()
        self.assertFalse(decoded.isNull(), reader.errorString())
        self.assertEqual((decoded.width(), decoded.height()), (image.width(), image.height()))
        return path.read_bytes(), decoded

    def test_set_dpi_returns_independent_image(self):
        original = self.make_image()
        original.setDotsPerMeterX(2000)
        original.setDotsPerMeterY(3000)
        result = set_image_dpi(original, 300)
        self.assertEqual((original.dotsPerMeterX(), original.dotsPerMeterY()), (2000, 3000))
        self.assertEqual((result.dotsPerMeterX(), result.dotsPerMeterY()), (11811, 11811))
        self.assertEqual(result.pixelColor(0, 0), original.pixelColor(0, 0))
        result.setPixelColor(0, 0, QColor("red"))
        self.assertNotEqual(result.pixelColor(0, 0), original.pixelColor(0, 0))
        x_dpi, y_dpi = get_image_dpi(result)
        self.assertAlmostEqual(x_dpi, 299.9994, places=4)
        self.assertAlmostEqual(y_dpi, 299.9994, places=4)

    def test_read_both_axes_independently(self):
        image = self.make_image()
        image.setDotsPerMeterX(5000)
        image.setDotsPerMeterY(10000)
        with localcontext() as context:
            context.prec = 2
            x_dpi, y_dpi = get_image_dpi(image)
        self.assertAlmostEqual(x_dpi, 127.0, places=6)
        self.assertAlmostEqual(y_dpi, 254.0, places=6)

    def test_invalid_image_dpi_and_excessive_side(self):
        with self.assertRaises(TypeError):
            set_image_dpi(object(), 300)
        with self.assertRaises(ValueError):
            set_image_dpi(QImage(), 300)
        with self.assertRaises(ValueError):
            get_image_dpi(QImage())
        image = self.make_image()
        with self.assertRaises(ValueError):
            set_image_dpi(image, 0)
        too_wide = self.make_image(MAX_SIDE_PX + 1, 1)
        with self.assertRaises(ValueError):
            set_image_dpi(too_wide, 300)
        image.setDotsPerMeterX(-1)
        with self.assertRaises(ValueError):
            get_image_dpi(image)

    def test_byte_limit_is_checked_before_copy(self):
        image = QImage(4, 3, QImage.Format.Format_RGBA64)
        self.assertFalse(image.isNull())
        image.fill(QColor("#123456"))
        original_density = (image.dotsPerMeterX(), image.dotsPerMeterY())
        self.assertGreater(image.sizeInBytes(), image.width() * image.height() * 4)
        with patch.object(photo_export, "MAX_IMAGE_BYTES", image.sizeInBytes() - 1):
            with self.assertRaisesRegex(ValueError, "байт"):
                set_image_dpi(image, 300)
        self.assertEqual((image.dotsPerMeterX(), image.dotsPerMeterY()), original_density)
        self.assertEqual(image.pixelColor(0, 0), QColor("#123456"))
        with patch.object(photo_export, "MAX_OUTPUT_PIXELS", image.width() * image.height() - 1):
            with self.assertRaisesRegex(ValueError, "пикселей"):
                set_image_dpi(image, 300)

    def test_byte_limit_accounts_for_copy_row_alignment(self):
        # Буфер пользователя может содержать менее четырёх байт на строку;
        # копия QImage дополняет каждую строку до 32-битной границы.
        storage = bytearray(6)
        image = QImage(storage, 3, 2, 3, QImage.Format.Format_Grayscale8)
        self.assertEqual(image.sizeInBytes(), 6)
        self.assertEqual(image.copy().sizeInBytes(), 8)
        with patch.object(photo_export, "MAX_IMAGE_BYTES", 7):
            with self.assertRaisesRegex(ValueError, "байт"):
                set_image_dpi(image, 300)
        with patch.object(photo_export, "MAX_IMAGE_BYTES", 8):
            result = set_image_dpi(image, 300)
            self.assertEqual(result.sizeInBytes(), 8)

    def test_png_file_contains_dpi_and_output_dimensions(self):
        with tempfile.TemporaryDirectory(prefix="photostudio-dpi-") as folder:
            for dpi in (300, 600):
                with self.subTest(dpi=dpi):
                    size = calculate_export_size(35, 45, "mm", dpi)
                    self.assertEqual((size.width_px, size.height_px),
                                     {300: (414, 532), 600: (827, 1063)}[dpi])
                    image = set_image_dpi(self.make_image(size.width_px, size.height_px), dpi)
                    data, decoded = self.write_and_read(Path(folder) / f"{dpi}.png", b"PNG", image)
                    x_dpm, y_dpm, unit = read_png_phys(data)
                    self.assertEqual(unit, 1)  # точки на метр
                    self.assertEqual((x_dpm, y_dpm), (dpi_to_dots_per_meter(dpi),) * 2)
                    self.assertAlmostEqual(x_dpm * 0.0254, dpi, delta=0.013)
                    self.assertAlmostEqual(y_dpm * 0.0254, dpi, delta=0.013)
                    x_dpi, y_dpi = get_image_dpi(decoded)
                    self.assertAlmostEqual(x_dpi, dpi, delta=0.05)
                    self.assertAlmostEqual(y_dpi, dpi, delta=0.05)

    def test_jpeg_file_contains_dpi_and_output_dimensions(self):
        with tempfile.TemporaryDirectory(prefix="photostudio-dpi-") as folder:
            for dpi in (127, 300, 600):
                with self.subTest(dpi=dpi):
                    size = calculate_export_size(35, 45, "mm", dpi)
                    self.assertEqual((size.width_px, size.height_px),
                                     {127: (175, 225), 300: (414, 532),
                                      600: (827, 1063)}[dpi])
                    image = set_image_dpi(self.make_image(size.width_px, size.height_px), dpi)
                    data, decoded = self.write_and_read(Path(folder) / f"{dpi}.jpg", b"JPEG", image)
                    unit, x_density, y_density, has_extra = read_jpeg_jfif(data)
                    # В синтетическом QImage не задаются EXIF/XMP. При их
                    # появлении нужно отдельно проверить возможный конфликт DPI.
                    self.assertFalse(has_extra, "JPEG содержит EXIF/XMP; проверьте DPI отдельно")
                    factor = 1.0 if unit == 1 else 2.54
                    self.assertAlmostEqual(x_density * factor, dpi, delta=0.05)
                    self.assertAlmostEqual(y_density * factor, dpi, delta=0.05)
                    x_dpi, y_dpi = get_image_dpi(decoded)
                    self.assertAlmostEqual(x_dpi, dpi, delta=0.05)
                    self.assertAlmostEqual(y_dpi, dpi, delta=0.05)

    def test_prepared_composite_in_real_png_and_jpeg(self):
        try:
            from PySide6.QtCore import QSize
            from photo_document import PhotoDocument, PhotoLayer
        except ImportError as error:
            self.skipTest(f"Для проверки композита нужен NumPy: {error}")

        source = QImage(36, 46, QImage.Format.Format_RGBA8888)
        source.fill(QColor(255, 0, 0, 128))
        source.setText("XML:com.adobe.xmp", "old resolution 72")
        for y in range(10):
            for x in range(10):
                source.setPixelColor(x, y, QColor(0, 0, 0, 0))
        document = PhotoDocument(source, QSize(36, 46), [PhotoLayer(source, layer_id="base")])
        png = prepare_export_image(document, 35, 45, "mm", 300, image_format="png")
        jpeg = prepare_export_image(document, 35, 45, "mm", 300, image_format="jpeg")
        self.assertEqual((png.width(), png.height()), (414, 532))
        self.assertEqual((jpeg.width(), jpeg.height()), (414, 532))
        self.assertEqual(png.pixelColor(0, 0), QColor("white"))
        self.assertEqual(png.pixelColor(50, 50).alpha(), 0)
        self.assertEqual(png.pixelColor(200, 250).getRgb(), (255, 0, 0, 128))
        self.assertEqual(jpeg.pixelColor(50, 50), QColor("white"))
        self.assertEqual(jpeg.pixelColor(200, 250).getRgb(), (255, 127, 127, 255))

        with tempfile.TemporaryDirectory(prefix="photostudio-composite-") as folder:
            folder = Path(folder)
            png_data, decoded_png = self.write_and_read(folder / "300.png", b"PNG", png)
            self.assertEqual(read_png_phys(png_data), (11811, 11811, 1))
            self.assertEqual(decoded_png.pixelColor(0, 0), QColor("white"))
            self.assertEqual(decoded_png.pixelColor(50, 50).alpha(), 0)
            self.assertEqual(decoded_png.pixelColor(200, 250).getRgb(), (255, 0, 0, 128))

            jpeg_data, decoded_jpeg = self.write_and_read(folder / "300.jpg", b"JPEG", jpeg)
            self.assertEqual(read_jpeg_jfif(jpeg_data), (1, 300, 300, False))
            self.assertFalse(decoded_jpeg.hasAlphaChannel())
            for position, expected in (((50, 50), (255, 255, 255)),
                                       ((200, 250), (255, 127, 127))):
                actual = decoded_jpeg.pixelColor(*position).getRgb()[:3]
                self.assertTrue(all(abs(a - e) <= 4 for a, e in zip(actual, expected)),
                                f"JPEG {position}: {actual} != {expected}")

            # Только метаданные: готовые пиксели не пересчитываются.
            with patch.object(photo_export, "_resample_composite",
                              side_effect=AssertionError("DPI-only change resampled")):
                png_600 = set_image_dpi(png, 600)
                jpeg_600 = set_image_dpi(jpeg, 600)
            self.assertEqual(bytes(png.constBits()), bytes(png_600.constBits()))
            self.assertEqual(bytes(jpeg.constBits()), bytes(jpeg_600.constBits()))
            changed_data, changed_decoded = self.write_and_read(folder / "600-only.png",
                                                                 b"PNG", png_600)
            self.assertEqual((changed_decoded.width(), changed_decoded.height()), (414, 532))
            self.assertEqual(read_png_phys(changed_data), (23622, 23622, 1))
            jpeg_600_data, jpeg_600_decoded = self.write_and_read(folder / "600-only.jpg",
                                                                   b"JPEG", jpeg_600)
            self.assertEqual((jpeg_600_decoded.width(), jpeg_600_decoded.height()), (414, 532))
            self.assertEqual(read_jpeg_jfif(jpeg_600_data), (1, 600, 600, False))

            # Повторный выбор физических размеров заново рассчитывает растр.
            selected_again = prepare_export_image(document, 35, 45, "mm", 600)
            selected_data, selected_decoded = self.write_and_read(folder / "600-new.png",
                                                                   b"PNG", selected_again)
            self.assertEqual((selected_decoded.width(), selected_decoded.height()), (827, 1063))
            self.assertEqual(read_png_phys(selected_data), (23622, 23622, 1))

    def test_dpi_limits_in_real_png_and_jpeg(self):
        with tempfile.TemporaryDirectory(prefix="photostudio-dpi-") as folder:
            for dpi in (MIN_DPI, 2, MAX_DPI):
                for fmt, extension in ((b"PNG", "png"), (b"JPEG", "jpg")):
                    with self.subTest(dpi=dpi, format=extension):
                        image = set_image_dpi(self.make_image(), dpi)
                        path = Path(folder) / f"{dpi}.{extension}"
                        data, decoded = self.write_and_read(path, fmt, image)
                        if fmt == b"PNG":
                            self.assertEqual(read_png_phys(data),
                                             (dpi_to_dots_per_meter(dpi),) * 2 + (1,))
                        else:
                            unit, x_density, y_density, has_extra = read_jpeg_jfif(data)
                            self.assertFalse(has_extra)
                            factor = 1.0 if unit == 1 else 2.54
                            self.assertAlmostEqual(x_density * factor, dpi, delta=0.05)
                            self.assertAlmostEqual(y_density * factor, dpi, delta=0.05)
                        x_dpi, y_dpi = get_image_dpi(decoded)
                        self.assertAlmostEqual(x_dpi, dpi, delta=0.05)
                        self.assertAlmostEqual(y_dpi, dpi, delta=0.05)

    def test_jpeg_codec_accepts_maximum_side_without_large_image(self):
        with tempfile.TemporaryDirectory(prefix="photostudio-dpi-") as folder:
            image = set_image_dpi(self.make_image(MAX_SIDE_PX, 1), 300)
            data, decoded = self.write_and_read(Path(folder) / "max-width.jpg", b"JPEG", image)
            self.assertEqual((decoded.width(), decoded.height()), (MAX_SIDE_PX, 1))
            self.assertEqual(read_jpeg_jfif(data)[:3], (1, 300, 300))


if __name__ == "__main__":
    unittest.main()
