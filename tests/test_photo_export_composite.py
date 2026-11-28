import gc
import unittest
from decimal import Decimal, Inexact, Rounded, ROUND_DOWN, localcontext
from fractions import Fraction
from unittest.mock import patch

import photo_export
from photo_export import (
    MAX_OUTPUT_PIXELS,
    MAX_SIDE_PX,
    calculate_export_size,
    dpi_to_dots_per_meter,
    estimate_print_size_mm,
    prepare_export_image,
    set_image_dpi,
)

try:
    import numpy as np
    from PySide6.QtCore import QRectF, QSize, Qt
    from PySide6.QtGui import QColor, QImage
except ImportError as error:
    DEPENDENCY_ERROR = f"{type(error).__name__}: {error}"
    np = QRectF = QSize = Qt = QColor = QImage = None
    CompositeRenderer = PhotoDocument = PhotoLayer = None
else:
    DEPENDENCY_ERROR = None
    # Ошибки самого модуля документа не должны маскироваться под skip.
    from photo_document import CompositeRenderer, PhotoDocument, PhotoLayer


def round_half_up(value):
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def ceil_fraction(value):
    return -(-value.numerator // value.denominator)


def rational_reference(pixels, target_width, target_height, scale):
    """Медленный независимый эталон для маленьких непрозрачных растров."""
    height, width = len(pixels), len(pixels[0])
    result = []
    for y in range(target_height):
        result_row = []
        for x in range(target_width):
            if scale < 1:
                left, right = Fraction(x) / scale, min(Fraction(x + 1) / scale, width)
                top, bottom = Fraction(y) / scale, min(Fraction(y + 1) / scale, height)
                samples = []
                first_y = top.numerator // top.denominator
                last_y = -(-bottom.numerator // bottom.denominator)
                first_x = left.numerator // left.denominator
                last_x = -(-right.numerator // right.denominator)
                for sy in range(first_y, min(height, last_y)):
                    wy = max(Fraction(0), min(bottom, sy + 1) - max(top, sy))
                    for sx in range(first_x, min(width, last_x)):
                        wx = max(Fraction(0), min(right, sx + 1) - max(left, sx))
                        if wx and wy:
                            samples.append((sy, sx, wx * wy / ((right - left) * (bottom - top))))
            else:
                cx = max(Fraction(0), min(Fraction(width - 1),
                         Fraction(2 * x + 1, 2) / scale - Fraction(1, 2)))
                cy = max(Fraction(0), min(Fraction(height - 1),
                         Fraction(2 * y + 1, 2) / scale - Fraction(1, 2)))
                x0, y0 = cx.numerator // cx.denominator, cy.numerator // cy.denominator
                fx, fy = cx - x0, cy - y0
                samples = [
                    (sy, sx, wx * wy)
                    for sy, wy in ((y0, 1 - fy), (min(y0 + 1, height - 1), fy))
                    for sx, wx in ((x0, 1 - fx), (min(x0 + 1, width - 1), fx))
                ]
            result_row.append(tuple(round_half_up(sum(
                Fraction(int(pixels[sy][sx][channel])) * weight
                for sy, sx, weight in samples
            )) for channel in range(4)))
        result.append(result_row)
    return result


@unittest.skipIf(DEPENDENCY_ERROR is not None,
                 f"Для проверки композита недоступны Qt/NumPy: {DEPENDENCY_ERROR}")
class ExportCompositeTests(unittest.TestCase):
    def image(self, width, height, color):
        result = QImage(width, height, QImage.Format.Format_RGBA8888)
        self.assertFalse(result.isNull())
        result.fill(QColor(color))
        return result

    def mask(self, width, height, value=255):
        result = QImage(width, height, QImage.Format.Format_Grayscale8)
        self.assertFalse(result.isNull())
        result.fill(value)
        return result

    def document(self, width, height, color="red"):
        image = self.image(width, height, color)
        return PhotoDocument(image, QSize(width, height),
                             [PhotoLayer(image, layer_id="base")])

    def document_from_pixels(self, pixels):
        height, width = pixels.shape[:2]
        image = QImage(pixels.data, width, height, pixels.strides[0],
                       QImage.Format.Format_RGBA8888).copy()
        return PhotoDocument(image, QSize(width, height),
                             [PhotoLayer(image, layer_id="base")])

    def pixel_signature(self, image):
        return (image.width(), image.height(), image.format(),
                image.dotsPerMeterX(), image.dotsPerMeterY(), image.devicePixelRatio(),
                tuple(image.pixelColor(x, y).getRgb()
                      for y in range(image.height()) for x in range(image.width())))

    def document_signature(self, document):
        return (
            id(document._original_image), self.pixel_signature(document.original_image),
            document.canvas_size.width(), document.canvas_size.height(),
            document.composition_rect.getRect(), document.source_path, document.metadata,
            tuple((id(layer), id(layer._image), id(layer._mask), layer.layer_id,
                   layer.name, layer.visible, layer.opacity, layer.metadata,
                   self.pixel_signature(layer.image), self.pixel_signature(layer.mask))
                  for layer in document.layers),
        )

    def assert_solid(self, image, rgba):
        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
        rows = np.frombuffer(converted.constBits(), dtype=np.uint8,
                             count=converted.sizeInBytes()).reshape(
                                 converted.height(), converted.bytesPerLine())
        pixels = rows[:, :converted.width() * 4].reshape(converted.height(),
                                                          converted.width(), 4)
        self.assertTrue(np.all(pixels == np.asarray(rgba, dtype=np.uint8)))

    def test_reference_physical_sizes_and_dpi(self):
        document = self.document(35, 45)
        for width, height, units, dpi, expected in (
            (35, 45, "mm", 300, (414, 532)),
            (35, 45, "mm", 600, (827, 1063)),
            (Decimal("3.5"), Decimal("4.5"), "cm", 300, (414, 532)),
        ):
            with self.subTest(units=units, dpi=dpi):
                output = prepare_export_image(document, width, height, units, dpi)
                self.assertEqual((output.width(), output.height()), expected)
                self.assertEqual(output.dotsPerMeterX(), dpi_to_dots_per_meter(dpi))
                self.assertEqual(output.dotsPerMeterY(), dpi_to_dots_per_meter(dpi))
                self.assert_solid(output, (255, 0, 0, 255))

        inches = self.document(7, 9)
        output = prepare_export_image(inches, 7, 9, "in", 1)
        self.assertEqual((output.width(), output.height()), (7, 9))
        self.assertEqual(output.dotsPerMeterX(), dpi_to_dots_per_meter(1))
        self.assertEqual(output.dotsPerMeterY(), dpi_to_dots_per_meter(1))

    def test_calculator_and_dpi_are_reused_one_composite_resampling(self):
        document = self.document(4, 2)
        for target, expected_resamples in (((4, 2), 0), ((8, 4), 1)):
            with self.subTest(target=target):
                with patch.object(photo_export, "calculate_export_size",
                                  wraps=calculate_export_size) as size_fn, \
                     patch.object(photo_export, "set_image_dpi",
                                  wraps=set_image_dpi) as dpi_fn, \
                     patch.object(photo_export, "_resample_composite",
                                  wraps=photo_export._resample_composite) as resampler:
                    output = prepare_export_image(document, *target, "px", 300)
                size_fn.assert_called_once_with(*target, "px", 300)
                self.assertEqual(dpi_fn.call_count, 1)
                self.assertEqual(resampler.call_count, expected_resamples)
                self.assertEqual((output.width(), output.height()), target)

    def test_renderer_receives_deep_snapshot_without_output_size(self):
        original = self.image(4, 2, "green")
        original.setDotsPerMeterX(4000)
        original.setDotsPerMeterY(5000)
        layers = [
            PhotoLayer(self.image(4, 2, "red"), layer_id="base", name="Фон",
                       mask=self.mask(4, 2, 210),
                       metadata={"nested": {"tags": ["base"]}}),
            PhotoLayer(self.image(4, 2, "blue"), layer_id="overlay", opacity=0.5,
                       metadata={"nested": {"tags": ["overlay"]}}),
        ]
        document = PhotoDocument(original, QSize(4, 2), layers,
                                 composition_rect=QRectF(0.25, 0.5, 3.5, 1.25),
                                 source_path="source.jpg",
                                 metadata={"nested": {"tags": ["document"]}})
        before = self.document_signature(document)
        copy_impl = PhotoDocument.copy
        renderer_impl = CompositeRenderer.render
        copy_calls, snapshots = [], []

        def copy_spy(source):
            self.assertIs(source, document)
            copy_calls.append(source)
            return copy_impl(source)

        def render_spy(renderer, snapshot, *args, **kwargs):
            self.assertIsNot(snapshot, document)
            self.assertEqual(args, ())
            self.assertEqual(kwargs, {})
            self.assertIsNot(snapshot.get_layer("base"), document.get_layer("base"))
            snapshots.append(snapshot)
            # И намеренные изменения внутри renderer затрагивают только снимок.
            snapshot._original_image.fill(QColor("yellow"))
            snapshot.get_layer("base")._image.fill(QColor("yellow"))
            snapshot.get_layer("base")._mask.fill(0)
            snapshot.get_layer("base")._metadata["nested"]["tags"].append("snapshot")
            snapshot._metadata["nested"]["tags"].append("snapshot")
            snapshot.get_layer("overlay").visible = False
            self.assertEqual(self.document_signature(document), before)
            return renderer_impl(renderer, snapshot)

        with patch.object(PhotoDocument, "copy", new=copy_spy), \
             patch.object(CompositeRenderer, "render", new=render_spy):
            output = prepare_export_image(document, 8, 4, "px", 300)
        self.assertEqual(len(copy_calls), 1)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(self.document_signature(document), before)
        result_pixel = output.pixelColor(0, 0).getRgb()
        snapshots[0].get_layer("base")._image.fill(QColor("cyan"))
        snapshots[0].get_layer("base")._mask.fill(255)
        self.assertEqual(output.pixelColor(0, 0).getRgb(), result_pixel)
        output.fill(QColor("yellow"))
        output.setDotsPerMeterX(1)
        self.assertEqual(self.document_signature(document), before)

    def test_result_remains_independent_after_original_changes_and_gc(self):
        for target in ((4, 2), (8, 4)):
            with self.subTest(target=target):
                document = self.document(4, 2)
                output = prepare_export_image(document, *target, "px", 300)
                gc.collect()
                self.assertEqual(output.pixelColor(0, 0).getRgb(), (255, 0, 0, 255))
                document.get_layer("base")._image.fill(QColor("yellow"))
                document.get_layer("base")._mask.fill(0)
                document._original_image.fill(QColor("blue"))
                self.assertEqual(output.pixelColor(0, 0).getRgb(), (255, 0, 0, 255))
                output.fill(QColor("cyan"))
                self.assertEqual(document.get_layer("base").image.pixelColor(0, 0),
                                 QColor("yellow"))
                self.assertEqual(document.get_layer("base").mask.pixelColor(0, 0).red(), 0)
                self.assertEqual(document.original_image.pixelColor(0, 0), QColor("blue"))

    def test_dpi_changes_metadata_only_not_source_or_px_dimensions(self):
        document = self.document(4, 2)
        before = self.document_signature(document)
        images = [prepare_export_image(document, 8, 4, "px", dpi)
                  for dpi in (72, 300, 600)]
        for image, dpi in zip(images, (72, 300, 600)):
            self.assertEqual((image.width(), image.height()), (8, 4))
            self.assertEqual(image.dotsPerMeterX(), dpi_to_dots_per_meter(dpi))
            self.assertEqual(image.dotsPerMeterY(), dpi_to_dots_per_meter(dpi))
            self.assert_solid(image, (255, 0, 0, 255))
        self.assertEqual(self.document_signature(document), before)

    def test_change_only_dpi_of_ready_raster_never_resamples(self):
        image = self.image(35, 45, "red")
        image.setPixelColor(0, 0, QColor("blue"))
        image.setPixelColor(34, 44, QColor(0, 255, 0, 128))
        document = PhotoDocument(image, QSize(35, 45), [PhotoLayer(image, layer_id="base")])
        before = self.document_signature(document)
        first = prepare_export_image(document, 35, 45, "mm", 300)
        first_pixels = bytes(first.constBits())
        with patch.object(photo_export, "_resample_composite",
                          side_effect=AssertionError("DPI-only change resampled")):
            changed = set_image_dpi(first, 600)
        self.assertEqual((first.width(), first.height()), (414, 532))
        self.assertEqual((changed.width(), changed.height()), (414, 532))
        self.assertEqual(bytes(changed.constBits()), first_pixels)
        self.assertEqual((first.dotsPerMeterX(), first.dotsPerMeterY()), (11811, 11811))
        self.assertEqual((changed.dotsPerMeterX(), changed.dotsPerMeterY()), (23622, 23622))
        for actual, expected in zip(estimate_print_size_mm(414, 532, 600),
                                    (17.526, 22.5213333333)):
            self.assertAlmostEqual(float(actual), expected, places=8)
        selected_again = prepare_export_image(document, 35, 45, "mm", 600)
        self.assertEqual((selected_again.width(), selected_again.height()), (827, 1063))
        self.assertEqual(self.document_signature(document), before)

    def test_fractional_and_full_pixel_gaps_are_not_aspect_errors(self):
        for sw, sh, tw, th in ((100, 150, 151, 226), (100, 101, 151, 153),
                               (74, 99, 3, 4), (149, 99, 3, 2),
                               (100, 101, 150, 152), (100, 101, 151, 154),
                               (100, 100, 200, 201)):
            with self.subTest(source=(sw, sh), target=(tw, th)):
                output = prepare_export_image(self.document(sw, sh), tw, th, "px", 300)
                self.assertEqual((output.width(), output.height()), (tw, th))
                self.assertEqual(output.pixelColor(tw // 2, th // 2).getRgb(),
                                 (255, 0, 0, 255))
        output = prepare_export_image(self.document(100, 100), 200, 201, "px", 300)
        self.assertEqual(output.pixelColor(0, 0).getRgb(), (255, 0, 0, 255))
        self.assertEqual(output.pixelColor(0, 200).getRgb(), (255, 255, 255, 255))

    def test_physical_ceil_is_exact_even_with_decimal_context_traps(self):
        document = self.document(100, 100)
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_DOWN
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            for value in (Decimal("100.249999999999999999999999999999999999"),
                          Decimal("100.25"),
                          Decimal("100.250000000000000000000000000000000001")):
                with self.subTest(height=value):
                    output = prepare_export_image(document, Decimal("99.75"), value, "in", 1)
                    self.assertEqual((output.width(), output.height()), (100, 101))
                    self.assertEqual(output.pixelColor(0, 0).getRgb(), (255, 0, 0, 255))
                    self.assertEqual(output.pixelColor(0, 100).getRgb(), (255, 255, 255, 255))

    def test_extreme_downsampling_only_resamples_nonempty_inner_raster(self):
        document = self.document(4, 4)
        cases = (
            ((1, 2), (0, 0), ((0, 1),)),
            ((2, 1), (0, 0), ((1, 0),)),
            ((1, 3), (0, 1), ((0, 0), (0, 2))),
            ((3, 1), (1, 0), ((0, 0), (2, 0))),
        )
        for target, red_pixel, white_pixels in cases:
            with self.subTest(target=target), np.errstate(all="raise"), \
                 patch.object(photo_export, "_resample_composite",
                              wraps=photo_export._resample_composite) as resampler:
                output = prepare_export_image(document, *target, "px", 300)
                self.assertEqual(resampler.call_args.args[1:], (1, 1, Fraction(1, 4)))
                self.assertEqual(output.pixelColor(*red_pixel).getRgb(), (255, 0, 0, 255))
                for position in white_pixels:
                    self.assertEqual(output.pixelColor(*position).getRgb(),
                                     (255, 255, 255, 255))

    def test_orientation_mismatch_centers_all_corners_without_stretching(self):
        image = self.image(35, 45, "red")
        image.setPixelColor(0, 0, QColor("blue"))
        image.setPixelColor(34, 0, QColor("lime"))
        image.setPixelColor(0, 44, QColor("yellow"))
        image.setPixelColor(34, 44, QColor("magenta"))
        document = PhotoDocument(image, QSize(35, 45), [PhotoLayer(image, layer_id="base")])
        before = self.document_signature(document)
        with patch.object(photo_export, "_resample_composite",
                          wraps=photo_export._resample_composite) as resampler:
            output = prepare_export_image(document, 45, 35, "mm", 300)
        self.assertEqual((output.width(), output.height()), (532, 414))
        self.assertEqual(resampler.call_args.args[1:], (322, 414, Fraction(46, 5)))
        for x in (0, 104, 427, 531):
            self.assertEqual(output.pixelColor(x, 200).getRgb(), (255, 255, 255, 255))
        for x, y, color in ((105, 0, "blue"), (426, 0, "lime"),
                            (105, 413, "yellow"), (426, 413, "magenta")):
            self.assertEqual(output.pixelColor(x, y), QColor(color))
        self.assertEqual(self.document_signature(document), before)

    def test_fractional_edge_uses_one_scale_without_adding_a_full_column(self):
        document = self.document(35, 45)
        with patch.object(photo_export, "_resample_composite",
                          wraps=photo_export._resample_composite) as resampler:
            output = prepare_export_image(document, 35, 45, "mm", 300)
        self.assertEqual(resampler.call_args.args[1:], (414, 532, Fraction(532, 45)))
        self.assertEqual((output.width(), output.height()), (414, 532))
        self.assert_solid(output, (255, 0, 0, 255))

    def test_fractional_frame_keeps_whole_canvas_white_area_and_corner(self):
        image = self.image(36, 46, "#222222")
        image.setPixelColor(0, 0, QColor("blue"))
        image.setPixelColor(35, 0, QColor("red"))
        image.setPixelColor(35, 45, QColor("lime"))
        white = self.image(36, 46, "white")
        outside_mask = self.mask(36, 46, 0)
        corner_mask = self.mask(36, 46, 0)
        for y in range(5, 9):
            for x in range(5, 9):
                outside_mask.setPixelColor(x, y, QColor("white"))
        for y in range(41, 46):
            for x in range(5):
                corner_mask.setPixelColor(x, y, QColor("white"))
        document = PhotoDocument(
            image, QSize(36, 46), [
                PhotoLayer(image, layer_id="working-image"),
                PhotoLayer(white, layer_id="white-canvas", mask=outside_mask),
                PhotoLayer(white, layer_id="protected-stamp-corner", mask=corner_mask),
            ], composition_rect=QRectF(20.2, 30.3, 35, 45),
        )
        before = self.document_signature(document)
        with patch.object(photo_export, "_resample_composite",
                          wraps=photo_export._resample_composite) as resampler:
            output = prepare_export_image(document, 35, 45, "mm", 300)
        self.assertEqual((output.width(), output.height()), (414, 532))
        self.assertEqual(resampler.call_args.args[1:], (414, 529, Fraction(23, 2)))
        # Три свободные строки: одна сверху, две снизу.
        for y in (0, 530, 531):
            self.assertEqual(output.pixelColor(200, y), QColor("white"))
        self.assertEqual(output.pixelColor(0, 1), QColor("blue"))
        self.assertEqual(output.pixelColor(413, 1), QColor("red"))
        self.assertEqual(output.pixelColor(0, 529), QColor("white"))
        self.assertEqual(output.pixelColor(413, 529), QColor("lime"))
        self.assertEqual(output.pixelColor(70, 71), QColor("white"))
        self.assertEqual(self.document_signature(document), before)

    def test_invalid_inputs_and_geometry_fail_before_snapshot_and_render(self):
        document = self.document(4, 2)
        with patch.object(PhotoDocument, "copy", side_effect=AssertionError("copy called")), \
             patch.object(CompositeRenderer, "render", side_effect=AssertionError("render called")):
            for width, height, units, dpi in ((0, 2, "px", 300), (4, 2, "m", 300),
                                             (4, 2, "px", 0),
                                             (10001, 4000, "px", 300)):
                with self.subTest(args=(width, height, units, dpi)):
                    with self.assertRaises(ValueError):
                        prepare_export_image(document, width, height, units, dpi)
            with self.assertRaisesRegex(TypeError, "PhotoDocument"):
                prepare_export_image(None, 4, 2, "px", 300)
            for image_format in ("gif", "", None, True):
                with self.subTest(image_format=image_format):
                    with self.assertRaisesRegex(ValueError, "PNG или JPEG"):
                        prepare_export_image(document, 4, 2, "px", 300,
                                             image_format=image_format)

    def test_oversized_canvas_fails_without_allocating_or_copying_it(self):
        original = self.image(1, 1, "red")
        for width, height in ((MAX_SIDE_PX + 1, 1), (10001, 4000)):
            with self.subTest(canvas=(width, height)):
                self.assertTrue(width > MAX_SIDE_PX or width * height > MAX_OUTPUT_PIXELS)
                document = PhotoDocument(original, QSize(width, height), [])
                with patch.object(PhotoDocument, "copy", side_effect=AssertionError("copy called")), \
                     patch.object(CompositeRenderer, "render", side_effect=AssertionError("render called")):
                    with self.assertRaisesRegex(ValueError, "Размер холста"):
                        prepare_export_image(document, 2, 1, "px", 300)

    def test_resampling_uses_one_scale_for_both_axes(self):
        pixels = np.zeros((101, 100, 4), dtype=np.uint8)
        pixels[:, :, 1] = np.arange(101, dtype=np.uint8)[:, None] * 2
        pixels[:, :, 3] = 255
        output = prepare_export_image(self.document_from_pixels(pixels), 151, 153, "px", 300)
        # s = 151/100. Если ошибочно взять s_y = 153/101, здесь было бы 19, а не 20.
        self.assertEqual(output.pixelColor(30, 15).getRgb(), (0, 20, 0, 255))
        self.assertEqual((output.width(), output.height()), (151, 153))

    def test_resampling_against_independent_exact_rational_reference(self):
        rng = np.random.default_rng(3821)
        cases = ((74, 99, 3, 4), (149, 99, 3, 2), (8, 8, 5, 5),
                 (6, 6, 1, 1), (3, 1, 6, 2), (4, 4, 1, 2),
                 (4, 4, 3, 1), (2, 1, 5, 5), (1, 2, 7, 3),
                 (2, 3, 67, 100), (4, 5, 8, 10), (1, 2, 2, 4))
        for sw, sh, tw, th in cases:
            with self.subTest(source=(sw, sh), target=(tw, th)):
                pixels = rng.integers(0, 256, size=(sh, sw, 4), dtype=np.uint8)
                pixels[:, :, 3] = 255
                scale = min(Fraction(tw, sw), Fraction(th, sh))
                inner_width = ceil_fraction(sw * scale)
                inner_height = ceil_fraction(sh * scale)
                left, top = (tw - inner_width) // 2, (th - inner_height) // 2
                inner = rational_reference(pixels, inner_width, inner_height, scale)
                output = prepare_export_image(self.document_from_pixels(pixels),
                                              tw, th, "px", 300)
                for y in range(th):
                    for x in range(tw):
                        if left <= x < left + inner_width and top <= y < top + inner_height:
                            expected = inner[y - top][x - left]
                        else:
                            expected = (255, 255, 255, 255)
                        actual = output.pixelColor(x, y).getRgb()
                        self.assertTrue(all(abs(a - e) <= 1 for a, e in zip(actual, expected)),
                                        f"{(sw, sh)}→{(tw, th)} at {(x, y)}: "
                                        f"{actual} != {expected}")

    def test_compositing_masks_happens_before_resampling(self):
        base = self.image(2, 2, "red")
        for y in range(2):
            base.setPixelColor(1, y, QColor("lime"))
        mask = self.mask(2, 2, 0)
        for y in range(2):
            mask.setPixelColor(0, y, QColor("white"))
        document = PhotoDocument(base, QSize(2, 2), [
            PhotoLayer(base, layer_id="base"),
            PhotoLayer(self.image(2, 2, "blue"), layer_id="blue", mask=mask),
        ])
        output = prepare_export_image(document, 4, 4, "px", 300)
        # Сначала синий/зелёный композит, затем интерполяция. Послойная
        # интерполяция и последующее сведение добавили бы здесь красный.
        self.assertEqual(output.pixelColor(1, 1).getRgb(), (0, 64, 191, 255))
        self.assertEqual(output.pixelColor(0, 0).getRgb(), (0, 0, 255, 255))
        self.assertEqual(output.pixelColor(3, 3).getRgb(), (0, 255, 0, 255))

    def test_visibility_opacity_and_grayscale_masks_are_respected(self):
        base = self.image(2, 2, "red")
        mask = self.mask(2, 2, 255)
        mask.setPixelColor(0, 0, QColor("black"))
        mask.setPixelColor(1, 0, QColor(128, 128, 128))
        document = PhotoDocument(base, QSize(2, 2), [
            PhotoLayer(base, layer_id="base"),
            PhotoLayer(self.image(2, 2, "blue"), layer_id="blue", mask=mask, opacity=0.5),
            PhotoLayer(self.image(2, 2, "lime"), layer_id="hidden", visible=False),
            PhotoLayer(self.image(2, 2, "yellow"), layer_id="zero", opacity=0),
        ])
        before = self.document_signature(document)
        native = prepare_export_image(document, 2, 2, "px", 300)
        enlarged = prepare_export_image(document, 4, 4, "px", 300)
        self.assertEqual(native.pixelColor(0, 0).getRgb(), (255, 0, 0, 255))
        for actual, expected in ((native.pixelColor(1, 0).getRgb(), (191, 0, 64, 255)),
                                 (native.pixelColor(0, 1).getRgb(), (127, 0, 128, 255))):
            self.assertTrue(all(abs(a - e) <= 1 for a, e in zip(actual, expected)))
        self.assertEqual(enlarged.pixelColor(0, 0), native.pixelColor(0, 0))
        self.assertEqual(enlarged.pixelColor(3, 0), native.pixelColor(1, 0))
        self.assertEqual(enlarged.pixelColor(0, 3), native.pixelColor(0, 1))
        self.assertEqual(self.document_signature(document), before)

    def test_transparency_resampling_uses_premultiplied_colors(self):
        red = self.image(2, 2, "red")
        mask = self.mask(2, 2, 0)
        mask.setPixelColor(0, 0, QColor("white"))
        document = PhotoDocument(red, QSize(2, 2),
                                 [PhotoLayer(red, layer_id="red", mask=mask)])
        reduced = prepare_export_image(document, 1, 1, "px", 300)
        self.assertEqual(reduced.pixelColor(0, 0).getRgb(), (255, 0, 0, 64))
        enlarged = prepare_export_image(document, 4, 4, "px", 300)
        self.assertEqual(enlarged.pixelColor(1, 1).getRgb(), (255, 0, 0, 143))
        self.assertEqual(enlarged.pixelColor(3, 3).alpha(), 0)

    def test_white_padding_preserves_png_alpha_and_jpeg_flattens_on_white(self):
        image = self.image(2, 2, "transparent")
        image.setPixelColor(0, 0, QColor(255, 0, 0, 128))
        image.setPixelColor(1, 0, QColor("lime"))
        image.setPixelColor(1, 1, QColor(0, 0, 255, 64))
        document = PhotoDocument(image, QSize(2, 2), [PhotoLayer(image, layer_id="base")])
        before = self.document_signature(document)
        png = prepare_export_image(document, 4, 8, "px", 300, image_format="PNG")
        jpeg = prepare_export_image(document, 4, 8, "px", 300, image_format="JpEg")
        jpg = prepare_export_image(document, 4, 8, "px", 300, image_format="jpg")
        self.assertTrue(png.hasAlphaChannel())
        self.assertFalse(jpeg.hasAlphaChannel())
        self.assertEqual((jpeg.dotsPerMeterX(), jpeg.dotsPerMeterY()), (11811, 11811))
        for result in (png, jpeg, jpg):
            self.assertEqual((result.width(), result.height()), (4, 8))
            for y in (0, 1, 6, 7):
                self.assertEqual(result.pixelColor(0, y), QColor("white"))
        self.assertEqual(png.pixelColor(0, 2).getRgb(), (255, 0, 0, 128))
        self.assertEqual(png.pixelColor(3, 2).getRgb(), (0, 255, 0, 255))
        self.assertEqual(png.pixelColor(0, 5).alpha(), 0)
        self.assertEqual(png.pixelColor(3, 5).getRgb(), (0, 0, 255, 64))
        for result in (jpeg, jpg):
            self.assertEqual(result.pixelColor(0, 2).getRgb(), (255, 127, 127, 255))
            self.assertEqual(result.pixelColor(3, 2).getRgb(), (0, 255, 0, 255))
            self.assertEqual(result.pixelColor(0, 5), QColor("white"))
            self.assertEqual(result.pixelColor(3, 5).getRgb(), (191, 191, 255, 255))
        # Единичный масштаб: только поля, без ресэмплинга композита.
        with patch.object(photo_export, "_resample_composite",
                          side_effect=AssertionError("unit scale resampled")):
            padded_only = prepare_export_image(document, 4, 2, "px", 300)
            jpeg_native = prepare_export_image(document, 2, 2, "px", 300,
                                               image_format="jpeg")
        self.assertEqual(padded_only.pixelColor(0, 0), QColor("white"))
        self.assertEqual(padded_only.pixelColor(1, 0).getRgb(), (255, 0, 0, 128))
        self.assertEqual(padded_only.pixelColor(1, 1).alpha(), 0)
        self.assertEqual(padded_only.pixelColor(3, 0), QColor("white"))
        self.assertEqual(jpeg_native.pixelColor(0, 0).getRgb(), (255, 127, 127, 255))
        self.assertEqual(jpeg_native.pixelColor(0, 1), QColor("white"))
        self.assertEqual(self.document_signature(document), before)

    def test_fractional_edge_keeps_uniform_partial_transparency(self):
        image = QImage(100, 101, QImage.Format.Format_RGBA8888)
        image.fill(QColor(255, 0, 0, 128))
        document = PhotoDocument(image, QSize(100, 101),
                                 [PhotoLayer(image, layer_id="base")])
        output = prepare_export_image(document, 151, 153, "px", 300)
        self.assert_solid(output, (255, 0, 0, 128))

    def test_empty_or_fully_masked_document_stays_transparent(self):
        original = self.image(4, 4, "red")
        documents = (
            PhotoDocument(original, QSize(4, 4), []),
            PhotoDocument(original, QSize(4, 4), [
                PhotoLayer(original, layer_id="masked", mask=self.mask(4, 4, 0)),
            ]),
        )
        for document in documents:
            for target in ((4, 4), (8, 8), (2, 2)):
                with self.subTest(layers=len(document.layers), target=target):
                    output = prepare_export_image(document, *target, "px", 300)
                    self.assert_solid(output, (0, 0, 0, 0))

    def test_corner_layer_and_all_edges_survive_without_crop_or_borders(self):
        base = self.image(20, 20, "#222222")
        for y in (0, 1):
            for x in (0, 1):
                base.setPixelColor(x, y, QColor("red"))
                base.setPixelColor(19 - x, y, QColor("lime"))
                base.setPixelColor(19 - x, 19 - y, QColor("blue"))
        corner = QImage(20, 20, QImage.Format.Format_RGBA8888)
        corner.fill(Qt.GlobalColor.transparent)
        for y in range(14, 20):
            for x in range(y - 14 + 1):
                corner.setPixelColor(x, y, QColor("white"))
        document = PhotoDocument(base, QSize(20, 20), [
            PhotoLayer(base, layer_id="base"),
            PhotoLayer(corner, layer_id="corner", metadata={"role": "corner"}),
        ])
        before = self.document_signature(document)
        for side in (10, 40):
            with self.subTest(side=side):
                output = prepare_export_image(document, side, side, "px", 300)
                self.assertEqual(output.pixelColor(0, 0).getRgb(), (255, 0, 0, 255))
                self.assertEqual(output.pixelColor(side - 1, 0).getRgb(), (0, 255, 0, 255))
                self.assertEqual(output.pixelColor(0, side - 1).getRgb(), (255, 255, 255, 255))
                self.assertEqual(output.pixelColor(side - 1, side - 1).getRgb(), (0, 0, 255, 255))
        self.assertEqual(self.document_signature(document), before)

    def test_fractional_edge_rounding_keeps_all_four_corner_markers(self):
        for sw, sh, tw, th in ((301, 361, 150, 180), (101, 121, 151, 181)):
            with self.subTest(source=(sw, sh), target=(tw, th)):
                image = self.image(sw, sh, "#222222")
                for y in range(5):
                    for x in range(5):
                        image.setPixelColor(x, y, QColor("red"))
                        image.setPixelColor(sw - 1 - x, y, QColor("lime"))
                        image.setPixelColor(x, sh - 1 - y, QColor("white"))
                        image.setPixelColor(sw - 1 - x, sh - 1 - y, QColor("blue"))
                document = PhotoDocument(image, QSize(sw, sh),
                                         [PhotoLayer(image, layer_id="base")])
                output = prepare_export_image(document, tw, th, "px", 300)
                self.assertEqual(output.pixelColor(0, 0).getRgb(), (255, 0, 0, 255))
                self.assertEqual(output.pixelColor(tw - 1, 0).getRgb(), (0, 255, 0, 255))
                self.assertEqual(output.pixelColor(0, th - 1).getRgb(), (255, 255, 255, 255))
                self.assertEqual(output.pixelColor(tw - 1, th - 1).getRgb(), (0, 0, 255, 255))

    def test_area_downsampling_gives_a_single_edge_pixel_a_contribution(self):
        image = self.image(100, 100, "black")
        image.setPixelColor(0, 99, QColor("white"))
        document = PhotoDocument(image, QSize(100, 100),
                                 [PhotoLayer(image, layer_id="base")])
        output = prepare_export_image(document, 10, 10, "px", 300)
        self.assertEqual(output.pixelColor(0, 9).getRgb(), (3, 3, 3, 255))
        self.assertEqual(output.pixelColor(1, 9).getRgb(), (0, 0, 0, 255))

    def test_invalid_renderer_result_is_reported(self):
        document = self.document(4, 2)
        before = self.document_signature(document)
        with patch.object(CompositeRenderer, "render", return_value=QImage()):
            with self.assertRaisesRegex(MemoryError, "композит"):
                prepare_export_image(document, 4, 2, "px", 300)
        with patch.object(CompositeRenderer, "render", return_value=self.image(2, 2, "red")):
            with self.assertRaisesRegex(RuntimeError, "Размер композита"):
                prepare_export_image(document, 4, 2, "px", 300)
        self.assertEqual(self.document_signature(document), before)


if __name__ == "__main__":
    unittest.main()
