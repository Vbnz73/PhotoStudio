"""Integration tests through the real PhotoStudio action and image codecs."""

import io
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

# Do not require a display for the Qt widgets exercised here.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

from test_photo_export_dpi_files import read_jpeg_jfif, read_png_phys

try:
    import cv2  # noqa: F401 -- the editor's real background implementation
    import mediapipe  # noqa: F401 -- a real application dependency
    import numpy as np
    from PySide6.QtCore import QCoreApplication, QEvent, QRectF, QSettings, QSize, Qt, QTimer
    from PySide6.QtGui import QColor, QImage, QImageReader, QPixmap, QResizeEvent
    from PySide6.QtWidgets import (
        QApplication, QDialog, QDialogButtonBox, QFileDialog, QLabel, QMessageBox,
    )
except ImportError as error:
    DEPENDENCY_ERROR = f"{type(error).__name__}: {error}"
    studio = None
else:
    DEPENDENCY_ERROR = None
    # Shell thumbnails are Windows-only and unrelated to export. Everywhere
    # else, isolate only that platform integration; the editor, OpenCV,
    # MediaPipe, Qt dialogs and PNG/JPEG codecs remain real.
    if sys.platform != "win32":
        original_thumbnail_module = sys.modules.get("thumbnail_provider")
        thumbnail_module = types.ModuleType("thumbnail_provider")
        thumbnail_module.get_thumbnail = lambda *args, **kwargs: None
        sys.modules["thumbnail_provider"] = thumbnail_module
        try:
            import main as studio
        finally:
            if original_thumbnail_module is None:
                del sys.modules["thumbnail_provider"]
            else:
                sys.modules["thumbnail_provider"] = original_thumbnail_module
    else:
        import main as studio

    import photo_export
    from photo_export import calculate_export_size


@unittest.skipIf(DEPENDENCY_ERROR is not None,
                 f"Для GUI-экспорта недоступны зависимости: {DEPENDENCY_ERROR}")
class ExportUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="photostudio-ui-export-")
        self.folder = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {"APPDATA": str(self.folder)})
        self.env.start()
        # Avoid both real Windows application settings and a real format store.
        settings = QSettings(str(self.folder / "settings.ini"), QSettings.Format.IniFormat)
        with patch.object(studio, "QSettings", return_value=settings):
            with redirect_stdout(io.StringIO()):
                self.window = studio.PhotoStudio()
        self.target_format = {
            "name": "Тестовый формат 35×45",
            "width": 35,
            "height": 45,
            "units": "mm",
            "_favorite_id": "export-test-format",
            "corner_enabled": True,
            "corner_width": 13,
            "corner_height": 13,
            "corner_type": 4,
            "corner_shape": "Закруглённый срез",
        }
        self.window.formats.append(self.target_format)
        self.window.refresh_format_list()
        self.select_format("custom:export-test-format")

    def tearDown(self):
        self.window.background_border_preview_timer.stop()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.env.stop()
        self.temporary.cleanup()

    def select_format(self, identity):
        index = next(
            i for i in range(self.window.format_combo.count())
            if self.window.get_format_identity(self.window.format_combo.itemData(i)) == identity
        )
        self.window.format_combo.setCurrentIndex(index)
        self.assertEqual(
            self.window.get_format_identity(self.window.format_combo.currentData()), identity
        )

    def lock_synthetic_photo(self, *, large=False):
        width, height = (400, 400) if large else (30, 40)
        source = QImage(width, height, QImage.Format.Format_RGBA8888)
        source.fill(QColor("red"))
        if not large:
            for y in range(5, 10):
                for x in range(5, 10):
                    source.setPixelColor(x, y, QColor(0, 0, 0, 0))
        # The export must not propagate old, potentially conflicting metadata.
        source.setText("XML:com.adobe.xmp", "old resolution: 72")
        self.source_path = self.folder / "source.png"
        self.assertTrue(source.save(str(self.source_path), "PNG"))
        self.source_bytes = self.source_path.read_bytes()
        self.window.load_photo(str(self.source_path))
        self.window.photo_view.crop_rect = (
            QRectF(0, 0, 400, 400) if large else QRectF(-2, -3, 35, 45)
        )
        self.window.lock_composition()
        self.assertTrue(self.window.photo_view.composition_locked)
        self.assertIsNotNone(self.window.photo_document)
        return self.window.photo_document

    def export(self, path, *, dpi=300, recalculate=None,
               selected_filter=None, answer=None, accept_options=True,
               trigger_action=False, options_hook=None, file_hook=None):
        captured = {}
        if selected_filter is None:
            selected_filter = studio.EXPORT_PNG_FILTER
        answer = answer if answer is not None else QMessageBox.StandardButton.Yes

        def exec_options(dialog):
            captured["dialog"] = dialog
            dialog.ppi_spin.setValue(dpi)
            if recalculate is not None:
                dialog.recalculate_check.setChecked(recalculate)
            if options_hook:
                options_hook(dialog)
            return QDialog.DialogCode.Accepted if accept_options else QDialog.DialogCode.Rejected

        def choose_file(*args, **kwargs):
            if file_hook:
                file_hook()
            return (str(path) if path is not None else "", selected_filter)

        with patch.object(studio.ExportOptionsDialog, "exec", new=exec_options), \
             patch.object(studio.QFileDialog, "getSaveFileName", side_effect=choose_file) as file_dialog, \
             patch.object(studio.QMessageBox, "warning") as warning, \
             patch.object(studio.QMessageBox, "critical") as critical, \
             patch.object(studio.QMessageBox, "question", return_value=answer) as question:
            if trigger_action:
                file_menu = self.window.menuBar().actions()[0].menu()
                action = next(a for a in file_menu.actions() if a.text() == "Экспорт композиции…")
                captured["shortcut"] = action.shortcut().toString()
                action.trigger()
            else:
                self.window.export_composition()
        captured.update(file_dialog=file_dialog, warning=warning, critical=critical, question=question)
        return captured

    def assert_export_succeeded(self, result):
        self.assertFalse(result["warning"].called, result["warning"].call_args_list)
        self.assertFalse(result["critical"].called, result["critical"].call_args_list)

    def read_image(self, path):
        reader = QImageReader(str(path))
        image = reader.read()
        self.assertFalse(image.isNull(), reader.errorString())
        return image

    def assert_rgb_close(self, image, point, expected, tolerance=5):
        actual = image.pixelColor(*point).getRgb()[:3]
        self.assertTrue(all(abs(a - e) <= tolerance for a, e in zip(actual, expected)),
                        f"{point}: {actual} != {expected}")

    def test_initial_window_can_resize_before_opening_a_photo(self):
        event = QResizeEvent(QSize(800, 600), QSize(700, 500))
        self.window.resizeEvent(event)
        self.assertTrue(self.window.current_pixmap.isNull())

    def test_real_qt_dialogs_select_jpeg_path_and_ppi(self):
        self.lock_synthetic_photo()
        self.window.show()
        self.app.processEvents()
        output = self.folder / "qt-dialog.jpg"
        calls = []
        attribute = Qt.ApplicationAttribute.AA_DontUseNativeDialogs
        previous_attribute = QApplication.testAttribute(attribute)
        QApplication.setAttribute(attribute, True)
        watchdog = QTimer(self.window)
        watchdog.setSingleShot(True)

        def abort_if_stuck():
            calls.append("timeout")
            dialog = self.app.activeModalWidget()
            if dialog is not None:
                dialog.reject()

        def select_file():
            dialog = self.app.activeModalWidget()
            calls.append(type(dialog).__name__)
            if isinstance(dialog, QFileDialog):
                dialog.selectNameFilter(studio.EXPORT_JPEG_FILTER)
                dialog.selectFile(str(self.folder / "qt-dialog"))
                dialog.accept()
            elif dialog is not None:
                dialog.reject()

        def select_options():
            dialog = self.app.activeModalWidget()
            calls.append(type(dialog).__name__)
            if isinstance(dialog, studio.ExportOptionsDialog):
                dialog.recalculate_check.setChecked(True)
                dialog.ppi_spin.setValue(127)
                dialog.accept()
                QTimer.singleShot(0, select_file)
            elif dialog is not None:
                dialog.reject()

        watchdog.timeout.connect(abort_if_stuck)
        watchdog.start(5000)
        QTimer.singleShot(0, select_options)
        try:
            with patch.object(studio.QMessageBox, "warning") as warning, \
                 patch.object(studio.QMessageBox, "critical") as critical:
                action = next(a for a in self.window.menuBar().actions()[0].menu().actions()
                              if a.text() == "Экспорт композиции…")
                action.trigger()
            warning.assert_not_called()
            critical.assert_not_called()
        finally:
            watchdog.stop()
            QApplication.setAttribute(attribute, previous_attribute)
        self.assertEqual(calls, ["ExportOptionsDialog", "QFileDialog"])
        unit, x_density, y_density, has_extra = read_jpeg_jfif(output.read_bytes())
        self.assertFalse(has_extra)
        factor = 1.0 if unit == 1 else 2.54
        self.assertAlmostEqual(x_density * factor, 127, places=6)
        self.assertAlmostEqual(y_density * factor, 127, places=6)
        decoded = self.read_image(output)
        self.assertEqual((decoded.width(), decoded.height()), (175, 225))
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)

    def test_menu_saves_full_composition_as_real_png_and_jpeg(self):
        document = self.lock_synthetic_photo()
        before = self.window.composite_renderer.render(document)
        metadata_before = document.metadata
        png = self.folder / "result.png"
        result = self.export(self.folder / "result", trigger_action=True)
        self.assert_export_succeeded(result)
        self.assertEqual(result["shortcut"], "Ctrl+Shift+S")
        self.assertEqual(result["file_dialog"].call_count, 1)
        dialog_args = result["file_dialog"].call_args.args
        self.assertIn("source_готово", dialog_args[2])
        self.assertEqual(dialog_args[5], studio.QFileDialog.Option.DontConfirmOverwrite)
        self.assertEqual(read_png_phys(png.read_bytes()), (11811, 11811, 1))
        decoded_png = self.read_image(png)
        self.assertEqual((decoded_png.width(), decoded_png.height()), (414, 532))
        self.assertEqual(decoded_png.pixelColor(5, 250), QColor("white"))  # outside the source
        self.assertEqual(decoded_png.pixelColor(100, 115).alpha(), 0)  # internal transparency
        self.assertEqual(decoded_png.pixelColor(250, 250), QColor("red"))
        self.assertEqual(decoded_png.pixelColor(60, 470), QColor("white"))  # protected corner

        # Overlays belong to the widget, not the saved document.
        self.window.photo_view.background_mask = np.zeros((45, 35), dtype=np.uint8)
        self.window.photo_view.background_brush_enabled = True
        jpeg = self.folder / "result-jpeg.jpg"
        result = self.export(self.folder / "result-jpeg", selected_filter=studio.EXPORT_JPEG_FILTER)
        self.assert_export_succeeded(result)
        self.assertEqual(read_jpeg_jfif(jpeg.read_bytes()), (1, 300, 300, False))
        decoded_jpeg = self.read_image(jpeg)
        self.assertEqual((decoded_jpeg.width(), decoded_jpeg.height()), (414, 532))
        self.assertFalse(decoded_jpeg.hasAlphaChannel())
        for point, color in (((5, 250), (255, 255, 255)),
                             ((100, 115), (255, 255, 255)),
                             ((250, 250), (255, 0, 0)),
                             ((60, 470), (255, 255, 255))):
            self.assert_rgb_close(decoded_jpeg, point, color)
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)
        self.assertEqual(document.metadata, metadata_before)
        self.assertEqual(self.window.composite_renderer.render(document), before)
        self.assertTrue(self.window.photo_view.composition_locked)
        self.assertIn(str(jpeg), self.window.statusBar().currentMessage())

    def test_fractional_frame_exports_full_aligned_canvas_without_cropping(self):
        plain_format = dict(self.target_format, name="Без уголка", _favorite_id="plain",
                            corner_enabled=False)
        self.window.formats.append(plain_format)
        self.window.refresh_format_list()
        self.select_format("custom:plain")
        source = QImage(36, 46, QImage.Format.Format_RGBA8888)
        source.fill(QColor("red"))
        for y in range(46):
            source.setPixelColor(35, y, QColor("blue"))
        for x in range(36):
            source.setPixelColor(x, 45, QColor("lime"))
        self.source_path = self.folder / "fractional-source.png"
        self.assertTrue(source.save(str(self.source_path), "PNG"))
        self.source_bytes = self.source_path.read_bytes()
        self.window.load_photo(str(self.source_path))
        self.window.photo_view.crop_rect = QRectF(0.25, 0.5, 35, 45)
        self.window.lock_composition()
        document = self.window.photo_document
        self.assertEqual((document.canvas_size.width(), document.canvas_size.height()), (36, 46))
        self.assertEqual(document.composition_rect, QRectF(0.25, 0.5, 35, 45))

        output = self.folder / "fractional.png"
        result = self.export(output)
        self.assert_export_succeeded(result)
        image = self.read_image(output)
        self.assertEqual((image.width(), image.height()), (414, 532))
        self.assertEqual(image.pixelColor(100, 0), QColor("white"))
        self.assertEqual(image.pixelColor(100, 1), QColor("red"))
        self.assertEqual(image.pixelColor(413, 100), QColor("blue"))
        self.assertEqual(image.pixelColor(100, 529), QColor("lime"))
        self.assertEqual(image.pixelColor(100, 530), QColor("white"))
        self.assertEqual(image.pixelColor(100, 531), QColor("white"))
        self.assertEqual(read_png_phys(output.read_bytes()), (11811, 11811, 1))
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)

    def test_first_ppi_change_keeps_selected_default_raster(self):
        self.lock_synthetic_photo()
        output = self.folder / "first-at-600.png"
        result = self.export(output, dpi=600)
        self.assert_export_succeeded(result)
        self.assertFalse(result["dialog"].recalculate_check.isChecked())
        self.assertEqual((self.read_image(output).width(), self.read_image(output).height()), (414, 532))
        self.assertEqual(read_png_phys(output.read_bytes()), (23622, 23622, 1))
        self.assertIn("17.526000 × 22.521333 мм", result["dialog"].preview_label.text())

    def test_png_ppi_only_keeps_raster_without_resampling_and_reselection_resamples(self):
        self.lock_synthetic_photo()
        first = self.folder / "300.png"
        result = self.export(first)
        self.assert_export_succeeded(result)
        second = self.folder / "600-ppi-only.png"
        with patch.object(photo_export, "_resample_composite",
                          side_effect=AssertionError("PPI-only change resampled")), \
             patch.object(studio, "prepare_export_image",
                          side_effect=AssertionError("PPI-only change rebuilt raster")):
            result = self.export(second, dpi=600)
        self.assert_export_succeeded(result)
        self.assertFalse(result["dialog"].recalculate_check.isChecked())
        self.assertIn("17.526000 × 22.521333 мм", result["dialog"].preview_label.text())
        self.assertEqual(read_png_phys(second.read_bytes()), (23622, 23622, 1))
        image1, image2 = self.read_image(first), self.read_image(second)
        self.assertEqual((image2.width(), image2.height()), (414, 532))
        self.assertEqual(bytes(image1.constBits()), bytes(image2.constBits()))

        third = self.folder / "600-format-reselected.png"
        with patch.object(photo_export, "_resample_composite",
                          wraps=photo_export._resample_composite) as resample:
            result = self.export(third, dpi=600, recalculate=True)
        self.assert_export_succeeded(result)
        self.assertGreater(resample.call_count, 0)
        image3 = self.read_image(third)
        self.assertEqual((image3.width(), image3.height()), (827, 1063))
        self.assertEqual(read_png_phys(third.read_bytes()), (23622, 23622, 1))
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)

    def test_extreme_integer_ppi_metadata_through_gui(self):
        pixel_format = dict(self.target_format, name="10×10 px", width=10, height=10,
                            units="px", _favorite_id="pixel-format", corner_enabled=False)
        self.window.formats.append(pixel_format)
        self.window.refresh_format_list()
        self.select_format("custom:pixel-format")
        self.lock_synthetic_photo()
        png = self.folder / "maximum-ppi.png"
        result = self.export(png, dpi=65535)
        self.assert_export_succeeded(result)
        dpm = calculate_export_size(10, 10, "px", 65535).dots_per_meter
        self.assertEqual(read_png_phys(png.read_bytes()), (dpm, dpm, 1))
        decoded_png = self.read_image(png)
        self.assertEqual((decoded_png.width(), decoded_png.height()), (10, 10))

        jpeg = self.folder / "minimum-ppi.jpg"
        result = self.export(jpeg, dpi=1, selected_filter=studio.EXPORT_JPEG_FILTER)
        self.assert_export_succeeded(result)
        self.assertEqual(read_jpeg_jfif(jpeg.read_bytes()), (1, 1, 1, False))
        decoded_jpeg = self.read_image(jpeg)
        self.assertEqual((decoded_jpeg.width(), decoded_jpeg.height()), (10, 10))

    def test_jpeg_ppi_only_keeps_raster_without_resampling(self):
        self.lock_synthetic_photo()
        first, second = self.folder / "300.jpg", self.folder / "600.jpg"
        result = self.export(first, selected_filter=studio.EXPORT_JPEG_FILTER)
        self.assert_export_succeeded(result)
        with patch.object(photo_export, "_resample_composite",
                          side_effect=AssertionError("PPI-only change resampled")), \
             patch.object(studio, "prepare_export_image",
                          side_effect=AssertionError("PPI-only change rebuilt raster")):
            result = self.export(second, dpi=600, selected_filter=studio.EXPORT_JPEG_FILTER)
        self.assert_export_succeeded(result)
        self.assertEqual(result["file_dialog"].call_args.args[4], studio.EXPORT_JPEG_FILTER)
        self.assertEqual(read_jpeg_jfif(second.read_bytes()), (1, 600, 600, False))
        image1, image2 = self.read_image(first), self.read_image(second)
        self.assertEqual((image2.width(), image2.height()), (414, 532))
        self.assertEqual(bytes(image1.constBits()), bytes(image2.constBits()))
        # Some platform dialogs may return an empty selected-filter string.
        with patch.object(photo_export, "_resample_composite",
                          side_effect=AssertionError("PPI-only change resampled")):
            result = self.export(self.folder / "empty-filter", dpi=600, selected_filter="")
        self.assert_export_succeeded(result)
        self.assertEqual(read_jpeg_jfif((self.folder / "empty-filter.jpg").read_bytes()),
                         (1, 600, 600, False))

    def test_changed_layer_invalidates_cached_raster(self):
        document = self.lock_synthetic_photo()
        first, second = self.folder / "with-corner.png", self.folder / "without-corner.png"
        self.assert_export_succeeded(self.export(first))
        document.get_layer("protected-stamp-corner").visible = False
        with patch.object(studio, "prepare_export_image", wraps=studio.prepare_export_image) as prepare:
            result = self.export(second, dpi=600)
        self.assert_export_succeeded(result)
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual(self.read_image(first).pixelColor(60, 470), QColor("white"))
        self.assertEqual(self.read_image(second).pixelColor(60, 470), QColor("red"))
        self.assertEqual(read_png_phys(second.read_bytes()), (23622, 23622, 1))

    def test_pending_background_slider_is_applied_before_export(self):
        document = self.lock_synthetic_photo(large=True)
        self.window.background_source_pixmap = QPixmap.fromImage(
            document.get_layer("working-image").image
        )
        mask = np.zeros((400, 400), dtype=np.uint8)
        mask[:, :200] = 255
        self.window._set_background_mask_edit(mask)
        self.window.background_replacement_color = QColor("blue")
        self.window._refresh_background_border_preview()
        old_mask_value = document.get_layer("working-image").mask.pixelColor(199, 200).red()
        self.assertEqual(old_mask_value, 255)
        self.window.background_border_slider.setValue(100)
        self.assertTrue(self.window.background_border_preview_timer.isActive())
        output = self.folder / "pending.png"
        with patch.object(self.window, "_refresh_background_border_preview",
                          wraps=self.window._refresh_background_border_preview) as refresh:
            result = self.export(output)
        self.assert_export_succeeded(result)
        refresh.assert_called_once()
        self.assertFalse(self.window.background_border_preview_timer.isActive())
        new_mask_value = document.get_layer("working-image").mask.pixelColor(199, 200).red()
        self.assertLess(new_mask_value, old_mask_value)
        decoded = self.read_image(output)
        self.assertEqual(read_png_phys(output.read_bytes()), (11811, 11811, 1))
        edge = decoded.pixelColor(206, 266)
        self.assertGreater(edge.blue(), 0)
        self.assertGreater(edge.red(), 0)
        expected = photo_export.prepare_export_image(document, 414, 532, "px", 300)
        self.assertEqual(decoded.pixelColor(206, 266), expected.pixelColor(206, 266))

    def test_failed_background_refresh_remains_pending_for_retry(self):
        document = self.lock_synthetic_photo(large=True)
        self.window.background_source_pixmap = QPixmap.fromImage(
            document.get_layer("working-image").image
        )
        mask = np.zeros((400, 400), dtype=np.uint8)
        mask[:, :200] = 255
        self.window._set_background_mask_edit(mask)
        self.window._refresh_background_border_preview()
        self.window.background_border_slider.setValue(100)
        target = self.folder / "retry.png"
        with patch.object(self.window, "_refresh_background_border_preview",
                          side_effect=RuntimeError("refresh failed")):
            result = self.export(target)
        result["critical"].assert_called_once()
        self.assertIn("refresh failed", result["critical"].call_args.args[2])
        self.assertTrue(self.window.background_border_preview_timer.isActive())
        self.assertFalse(target.exists())
        result = self.export(target)
        self.assert_export_succeeded(result)
        self.assertLess(document.get_layer("working-image").mask.pixelColor(199, 200).red(), 255)
        self.assertEqual(read_png_phys(target.read_bytes()), (11811, 11811, 1))

    def test_slider_dragging_blocks_export_until_released(self):
        self.lock_synthetic_photo()
        self.window._on_background_border_slider_pressed()
        output = self.folder / "drag.png"
        result = self.export(output)
        result["warning"].assert_called_once()
        result["file_dialog"].assert_not_called()
        self.assertFalse(output.exists())
        self.window._on_background_border_slider_released()

    def test_locked_format_survives_later_selection_then_applies_after_unlock(self):
        document = self.lock_synthetic_photo()
        other = dict(self.target_format, name="Другой формат", width=30, height=40,
                     _favorite_id="other", corner_enabled=False)
        self.window.formats.append(other)
        self.window.refresh_format_list()
        self.select_format("custom:other")
        self.assertEqual(document.metadata["export_format"]["width"], 35)
        self.assertEqual(self.window.photo_view.format_width_mm, 35)
        output = self.folder / "locked.png"
        result = self.export(output)
        self.assert_export_succeeded(result)
        self.assertEqual((self.read_image(output).width(), self.read_image(output).height()), (414, 532))
        notices = [label.text() for label in result["dialog"].findChildren(QLabel)]
        self.assertTrue(any("Формат в списке изменён" in text for text in notices))
        self.assertEqual(self.read_image(output).pixelColor(60, 470), QColor("white"))

        self.window.unlock_composition()
        self.assertEqual((self.window.photo_view.format_width_mm,
                          self.window.photo_view.format_height_mm), (30, 40))
        self.assertIsNone(self.window._export_size)
        self.assertIsNone(self.window._export_cache)
        self.window.lock_composition()
        self.assertEqual(self.window.photo_document.metadata["export_format"]["width"], 30)
        self.select_format("custom:export-test-format")
        self.window.load_photo(str(self.source_path))
        self.assertIsNone(self.window.photo_document)
        self.assertEqual((self.window.photo_view.format_width_mm,
                          self.window.photo_view.format_height_mm), (35, 45))

    def test_same_dimensions_but_different_corner_keep_locked_composition(self):
        document = self.lock_synthetic_photo()
        other = dict(self.target_format, name="Без уголка", _favorite_id="same-size",
                     corner_enabled=False)
        self.window.formats.append(other)
        self.window.refresh_format_list()
        self.select_format("custom:same-size")
        self.assertTrue(document.metadata["export_format"]["corner_enabled"])
        self.assertTrue(document.get_layer("protected-stamp-corner").metadata["enabled"])
        original = self.folder / "original-corner.png"
        result = self.export(original)
        self.assert_export_succeeded(result)
        self.assertEqual(self.read_image(original).pixelColor(60, 470), QColor("white"))
        self.assertTrue(any("Формат в списке изменён" in label.text()
                            for label in result["dialog"].findChildren(QLabel)))

        self.window.unlock_composition()
        self.window.lock_composition()
        self.assertFalse(self.window.photo_document.metadata["export_format"]["corner_enabled"])
        self.assertFalse(self.window.photo_document.get_layer("protected-stamp-corner").metadata["enabled"])
        changed = self.folder / "new-corner.png"
        result = self.export(changed)
        self.assert_export_succeeded(result)
        self.assertEqual(self.read_image(changed).pixelColor(60, 470), QColor("red"))

    def test_options_show_ceiling_sizes_and_invalid_limits(self):
        dialog = studio.ExportOptionsDialog(self.target_format)
        self.assertEqual(dialog.selected_size(), calculate_export_size(35, 45, "mm", 300))
        self.assertFalse(dialog.recalculate_check.isChecked())
        self.assertTrue(dialog.recalculate_check.isEnabled())
        dialog.ppi_spin.setValue(600)
        self.assertEqual((dialog.selected_size().width_px, dialog.selected_size().height_px), (414, 532))
        dialog.recalculate_check.setChecked(True)
        self.assertEqual((dialog.selected_size().width_px, dialog.selected_size().height_px), (827, 1063))
        dialog.ppi_spin.setValue(65535)
        self.assertIsNone(dialog.selected_size())
        self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        self.assertIn("Невозможно", dialog.preview_label.text())

        previous = calculate_export_size(35, 45, "mm", 300)
        repeated = studio.ExportOptionsDialog(self.target_format, previous)
        repeated.ppi_spin.setValue(600)
        self.assertEqual((repeated.selected_size().width_px, repeated.selected_size().height_px), (414, 532))
        repeated.recalculate_check.setChecked(True)
        self.assertEqual((repeated.selected_size().width_px, repeated.selected_size().height_px), (827, 1063))
        invalid = studio.ExportOptionsDialog(dict(self.target_format, width=0))
        self.assertIsNone(invalid.selected_size())
        self.assertFalse(invalid.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())

        # If the physical format cannot fit at the default 300 PPI, there is
        # no initial raster to keep; changing PPI can make its selection valid.
        large = studio.ExportOptionsDialog(dict(self.target_format, width=6000, height=1))
        self.assertIsNone(large.selected_size())
        self.assertTrue(large.recalculate_check.isChecked())
        self.assertFalse(large.recalculate_check.isEnabled())
        large.ppi_spin.setValue(100)
        self.assertEqual((large.selected_size().width_px, large.selected_size().height_px), (23623, 4))
        self.assertTrue(large.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())

    def test_extension_selects_encoder_and_unknown_suffix_is_rejected(self):
        self.assertEqual(studio._export_target_path("a", studio.EXPORT_PNG_FILTER), ("a.png", "png"))
        self.assertEqual(studio._export_target_path("a", studio.EXPORT_JPEG_FILTER), ("a.jpg", "jpeg"))
        self.assertEqual(studio._export_target_path("a.JPEG", studio.EXPORT_PNG_FILTER), ("a.JPEG", "jpeg"))
        self.assertEqual(studio._export_target_path("a.PNG", studio.EXPORT_JPEG_FILTER), ("a.PNG", "png"))
        with self.assertRaises(ValueError):
            studio._export_target_path("a.bmp", studio.EXPORT_PNG_FILTER)
        self.lock_synthetic_photo()
        explicit = self.folder / "typed.JPEG"
        result = self.export(explicit, selected_filter=studio.EXPORT_PNG_FILTER)
        self.assert_export_succeeded(result)
        self.assertEqual(read_jpeg_jfif(explicit.read_bytes()), (1, 300, 300, False))
        invalid = self.folder / "bad.bmp"
        result = self.export(invalid)
        result["warning"].assert_called_once()
        self.assertFalse(invalid.exists())

    def test_no_composition_and_cancel_do_not_save_or_change_export_state(self):
        result = self.export(self.folder / "missing.png")
        result["warning"].assert_called_once()
        result["file_dialog"].assert_not_called()
        self.lock_synthetic_photo()
        result = self.export(self.folder / "cancel.png", accept_options=False)
        result["file_dialog"].assert_not_called()
        self.assertIsNone(self.window._export_size)
        result = self.export(None)
        self.assert_export_succeeded(result)
        self.assertIsNone(self.window._export_size)
        self.assertIsNone(self.window._export_cache)
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)

    def test_unicode_destination_and_filename(self):
        self.lock_synthetic_photo()
        folder = self.folder / "Папка с фотографиями"
        folder.mkdir()
        output = folder / "Готовая фотография.png"
        result = self.export(output)
        self.assert_export_succeeded(result)
        self.assertEqual(read_png_phys(output.read_bytes()), (11811, 11811, 1))
        self.assertEqual((self.read_image(output).width(), self.read_image(output).height()), (414, 532))
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)

    def test_existing_name_after_appending_extension_requires_confirmation(self):
        self.lock_synthetic_photo()
        target = self.folder / "existing.png"
        target.write_bytes(b"old file")
        result = self.export(self.folder / "existing", answer=QMessageBox.StandardButton.No)
        result["question"].assert_called_once()
        self.assertIn(str(target), result["question"].call_args.args[2])
        self.assertEqual(target.read_bytes(), b"old file")
        self.assertIsNone(self.window._export_size)
        result = self.export(self.folder / "existing", answer=QMessageBox.StandardButton.Yes)
        self.assert_export_succeeded(result)
        result["question"].assert_called_once()
        self.assertEqual(read_png_phys(target.read_bytes()), (11811, 11811, 1))

    def test_source_file_cannot_be_overwritten_even_if_confirmed(self):
        self.lock_synthetic_photo()
        result = self.export(self.source_path)
        result["warning"].assert_called_once()
        result["question"].assert_not_called()
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)
        self.assertIsNone(self.window._export_size)

    def test_source_hardlink_is_also_protected(self):
        self.lock_synthetic_photo()
        alias = self.folder / "hardlink.png"
        try:
            os.link(self.source_path, alias)
        except OSError as error:
            self.skipTest(f"Файловая система не поддерживает hardlink: {error}")
        result = self.export(alias)
        result["warning"].assert_called_once()
        result["question"].assert_not_called()
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)
        self.assertEqual(alias.read_bytes(), self.source_bytes)

    def test_source_symlink_is_protected(self):
        self.lock_synthetic_photo()
        alias = self.folder / "symbolic.png"
        try:
            os.symlink(self.source_path, alias)
        except OSError as error:
            self.skipTest(f"Символические ссылки недоступны: {error}")
        result = self.export(alias)
        result["warning"].assert_called_once()
        result["question"].assert_not_called()
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)
        self.assertTrue(alias.is_symlink())

    def test_renamed_current_source_is_protected(self):
        self.lock_synthetic_photo()
        renamed = self.folder / "renamed.png"
        self.source_path.rename(renamed)
        self.window.current_photo_path = str(renamed)
        result = self.export(renamed)
        result["warning"].assert_called_once()
        result["question"].assert_not_called()
        self.assertEqual(renamed.read_bytes(), self.source_bytes)

    def test_writer_failure_keeps_existing_destination_and_document(self):
        document = self.lock_synthetic_photo()
        before = self.window.composite_renderer.render(document)
        target = self.folder / "existing.png"
        target.write_bytes(b"must remain")
        with patch.object(studio, "QImageWriter") as writer:
            writer.return_value.write.return_value = False
            writer.return_value.errorString.return_value = "codec unavailable"
            result = self.export(target)
        result["critical"].assert_called_once()
        self.assertIn("codec unavailable", result["critical"].call_args.args[2])
        self.assertEqual(target.read_bytes(), b"must remain")
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)
        self.assertEqual(self.window.composite_renderer.render(document), before)
        self.assertIsNone(self.window._export_size)
        self.assertIsNone(self.window._export_cache)

    def test_commit_failure_keeps_existing_destination(self):
        self.lock_synthetic_photo()
        target = self.folder / "cannot-commit.png"
        target.write_bytes(b"old file")
        with patch.object(studio.QSaveFile, "commit", return_value=False) as commit:
            result = self.export(target)
        commit.assert_called_once()
        result["critical"].assert_called_once()
        self.assertEqual(target.read_bytes(), b"old file")
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)
        self.assertIsNone(self.window._export_size)

    def test_unwritable_destination_reports_error_without_success_state(self):
        self.lock_synthetic_photo()
        output = self.folder / "missing-directory" / "result.png"
        result = self.export(output)
        result["critical"].assert_called_once()
        self.assertFalse(output.exists())
        self.assertIsNone(self.window._export_size)
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)

    def test_document_changed_in_modal_dialog_is_not_saved(self):
        self.lock_synthetic_photo()
        output = self.folder / "stale.png"
        result = self.export(output, file_hook=self.window.unlock_composition)
        result["warning"].assert_called_once()
        self.assertFalse(output.exists())
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)

    def test_document_changed_in_options_does_not_open_file_dialog(self):
        self.lock_synthetic_photo()
        output = self.folder / "stale-options.png"
        result = self.export(output, options_hook=lambda _: self.window.unlock_composition())
        result["warning"].assert_called_once()
        result["file_dialog"].assert_not_called()
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
