import json
import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal, Inexact, Rounded, ROUND_DOWN, localcontext
from pathlib import Path

from photo_export import (
    MAX_DPI,
    MAX_IMAGE_BYTES,
    MAX_NUMBER_DIGITS,
    MAX_OUTPUT_PIXELS,
    MAX_SIDE_PX,
    MIN_DPI,
    MIN_SIDE_PX,
    SUPPORTED_UNITS,
    calculate_export_size,
    dpi_to_dots_per_meter,
    estimate_print_size_mm,
)


class ExportSizeTests(unittest.TestCase):
    def test_reference_sizes_and_supported_units(self):
        cases = [
            (35, 45, "mm", 300, (413, 531)),
            (35, 45, "mm", 600, (827, 1063)),
            (3.5, 4.5, "cm", 300, (413, 531)),
            (Decimal("3.5"), Decimal("4.5"), "cm", 600, (827, 1063)),
            (2, 2, "in", 300, (600, 600)),
            (600, 600, "px", 300, (600, 600)),
            (354.0, 472.0, "px", 300, (354, 472)),
            (45, 35, "mm", 300, (531, 413)),
        ]
        for width, height, units, dpi, expected in cases:
            with self.subTest(width=width, height=height, units=units, dpi=dpi):
                result = calculate_export_size(width, height, units, dpi)
                self.assertEqual((result.width_px, result.height_px), expected)
                self.assertEqual(result.dpi, dpi)
                self.assertEqual(result.dots_per_meter, dpi_to_dots_per_meter(dpi))
        self.assertEqual(SUPPORTED_UNITS, {"mm", "cm", "in", "px"})

    def test_pixels_do_not_change_with_dpi(self):
        results = [calculate_export_size(600.0, Decimal("600.0"), "px", dpi)
                   for dpi in (MIN_DPI, 72, 300, 600, MAX_DPI)]
        self.assertTrue(all((r.width_px, r.height_px) == (600, 600) for r in results))
        self.assertEqual([r.dpi for r in results], [MIN_DPI, 72, 300, 600, MAX_DPI])
        self.assertNotEqual(results[2].dots_per_meter, results[3].dots_per_meter)

    def test_round_half_up_at_exact_and_near_half_pixel(self):
        result = calculate_export_size(Decimal("0.5"), Decimal("1.5"), "in", 1)
        self.assertEqual((result.width_px, result.height_px), (1, 2))
        result = calculate_export_size(Decimal("12.7"), Decimal("38.1"), "mm", 1)
        self.assertEqual((result.width_px, result.height_px), (1, 2))
        result = calculate_export_size(
            Decimal("1.499999999999999999999999999999999999"),
            Decimal("1.500000000000000000000000000000000001"), "in", 1,
        )
        self.assertEqual((result.width_px, result.height_px), (1, 2))
        with self.assertRaises(ValueError):
            calculate_export_size(Decimal("0.499999999999999999999999999999999999"),
                                  1, "in", 1)

    def test_dots_per_meter(self):
        for dpi, expected in ((1, 39), (72, 2835), (96, 3780),
                              (127, 5000), (300, 11811), (600, 23622)):
            with self.subTest(dpi=dpi):
                self.assertEqual(dpi_to_dots_per_meter(dpi), expected)
        self.assertEqual(dpi_to_dots_per_meter(300.0), 11811)
        self.assertEqual(dpi_to_dots_per_meter(Decimal("300.0")), 11811)

    def test_estimate_print_size(self):
        self.assertEqual(estimate_print_size_mm(600, 600, 300),
                         (Decimal("50.8"), Decimal("50.8")))
        self.assertEqual(estimate_print_size_mm(600.0, 600, 600),
                         (Decimal("25.4"), Decimal("25.4")))
        width, height = estimate_print_size_mm(413, 531, 300)
        self.assertAlmostEqual(float(width), 34.9673333333, places=8)
        self.assertAlmostEqual(float(height), 44.958, places=8)

    def test_invalid_dimensions(self):
        invalid = (0, -1, True, False, None, "35", "bad", [],
                   float("nan"), float("inf"), float("-inf"),
                   Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"),
                   Decimal("-Infinity"))
        for value in invalid:
            for index in (0, 1):
                dimensions = [35, 45]
                dimensions[index] = value
                with self.subTest(value=repr(value), index=index):
                    with self.assertRaises(ValueError):
                        calculate_export_size(*dimensions, "mm", 300)

    def test_invalid_units_are_not_assumed_to_be_mm(self):
        for units in (None, "", "MM", "m", "pixels", "inch", [], True):
            with self.subTest(units=units):
                with self.assertRaises(ValueError):
                    calculate_export_size(35, 45, units, 300)

    def test_invalid_dpi(self):
        values = (0, -1, MIN_DPI - 1, MAX_DPI + 1, 300.5, Decimal("72.5"),
                  True, False, None, "300", float("nan"), float("inf"),
                  Decimal("NaN"), Decimal("Infinity"))
        for value in values:
            with self.subTest(dpi=repr(value)):
                with self.assertRaises(ValueError):
                    calculate_export_size(35, 45, "mm", value)
                with self.assertRaises(ValueError):
                    dpi_to_dots_per_meter(value)

    def test_fractional_pixels_are_rejected_not_rounded(self):
        for value in (0.5, 600.1, Decimal("600.0001")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    calculate_export_size(value, 600, "px", 300)
                with self.assertRaises(ValueError):
                    estimate_print_size_mm(600, value, 300)
        result = calculate_export_size(600.0, Decimal("600.000"), "px", 300)
        self.assertEqual((result.width_px, result.height_px), (600, 600))

    def test_rounded_zero_is_rejected(self):
        for units in ("mm", "cm", "in"):
            with self.subTest(units=units):
                with self.assertRaises(ValueError):
                    calculate_export_size(Decimal("0.001"), 1, units, 1)
        with self.assertRaises(ValueError):
            calculate_export_size(Decimal("1e-1000000"), 1, "in", 1)

    def test_side_limit_in_pixels_and_physical_units(self):
        self.assertEqual(MIN_SIDE_PX, 1)
        self.assertEqual(MAX_SIDE_PX, 65_500)
        result = calculate_export_size(MAX_SIDE_PX, 1, "px", 300)
        self.assertEqual((result.width_px, result.height_px), (MAX_SIDE_PX, 1))
        result = calculate_export_size(Decimal("65500.49"), 1, "in", 1)
        self.assertEqual(result.width_px, MAX_SIDE_PX)
        for width, units in ((MAX_SIDE_PX + 1, "px"),
                             (Decimal("65500.5"), "in"),
                             (Decimal("1e1000000"), "mm")):
            with self.subTest(width=width, units=units):
                with self.assertRaises(ValueError):
                    calculate_export_size(width, 1, units, 1)

    def test_area_limit_without_allocating_image(self):
        self.assertEqual(MAX_OUTPUT_PIXELS, 40_000_000)
        self.assertEqual(MAX_IMAGE_BYTES, 160_000_000)
        result = calculate_export_size(10_000, 4_000, "px", 300)
        self.assertEqual(result.width_px * result.height_px, MAX_OUTPUT_PIXELS)
        with self.assertRaises(ValueError):
            calculate_export_size(10_001, 4_000, "px", 300)
        with self.assertRaises(ValueError):
            calculate_export_size(40_001, 1_000, "in", 1)
        with self.assertRaises(ValueError):
            estimate_print_size_mm(10_001, 4_000, 300)

    def test_input_digit_limit_before_fraction_conversion(self):
        self.assertEqual(MAX_NUMBER_DIGITS, 1_000)
        at_limit = Decimal("1." + "0" * (MAX_NUMBER_DIGITS - 1))
        too_long = Decimal("1." + "0" * MAX_NUMBER_DIGITS)
        result = calculate_export_size(at_limit, 1, "in", 300)
        self.assertEqual((result.width_px, result.height_px), (300, 300))
        for operation in (
            lambda: calculate_export_size(too_long, 1, "in", 300),
            lambda: calculate_export_size(1, too_long, "px", 300),
            lambda: dpi_to_dots_per_meter(too_long),
            lambda: estimate_print_size_mm(too_long, 1, 300),
        ):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(ValueError, "цифр"):
                    operation()

    def test_decimal_context_does_not_change_calculations(self):
        expected_print_size = estimate_print_size_mm(413, 531, 300)
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_DOWN
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            result = calculate_export_size(Decimal("12.7"), Decimal("38.1"), "mm", 1)
            self.assertEqual((result.width_px, result.height_px), (1, 2))
            self.assertEqual(dpi_to_dots_per_meter(300), 11811)
            self.assertEqual(estimate_print_size_mm(413, 531, 300), expected_print_size)

    def test_result_is_immutable(self):
        result = calculate_export_size(35, 45, "mm", 300)
        with self.assertRaises(FrozenInstanceError):
            result.width_px = 1

    def test_existing_format_catalog_at_300_dpi(self):
        catalog_path = Path(__file__).resolve().parents[1] / "formats_ru.json"
        with catalog_path.open(encoding="utf-8-sig") as stream:
            formats = json.load(stream)["formats"]
        self.assertTrue(formats)
        for item in formats:
            with self.subTest(name=item.get("name")):
                result = calculate_export_size(
                    item["width"], item["height"], item.get("units", "mm"), 300,
                )
                self.assertGreaterEqual(result.width_px, MIN_SIDE_PX)
                self.assertGreaterEqual(result.height_px, MIN_SIDE_PX)
                self.assertLessEqual(result.width_px * result.height_px, MAX_OUTPUT_PIXELS)


if __name__ == "__main__":
    unittest.main()
