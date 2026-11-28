import unittest

try:
    import numpy as np
    from PySide6.QtCore import QRectF, QSize
    from PySide6.QtGui import QColor, QImage
except ImportError as error:
    DEPENDENCY_ERROR = f"{type(error).__name__}: {error}"
    np = QRectF = QSize = QColor = QImage = PhotoDocument = PhotoLayer = None
else:
    DEPENDENCY_ERROR = None
    # Ошибки самого модуля документа должны приводить к провалу тестов,
    # а не маскироваться под отсутствие тестовых зависимостей.
    from photo_document import PhotoDocument, PhotoLayer


@unittest.skipIf(DEPENDENCY_ERROR is not None,
                 f"Для проверки копирования недоступны Qt/NumPy: {DEPENDENCY_ERROR}")
class PhotoDocumentCopyTests(unittest.TestCase):
    def image(self, color, width=4, height=2):
        result = QImage(width, height, QImage.Format.Format_RGBA8888)
        self.assertFalse(result.isNull())
        result.fill(QColor(color))
        return result

    def mask(self, value, width=4, height=2):
        result = QImage(width, height, QImage.Format.Format_Grayscale8)
        self.assertFalse(result.isNull())
        result.fill(value)
        return result

    def document(self):
        layers = [
            PhotoLayer(self.image("red"), layer_id="red", mask=self.mask(128),
                       opacity=0.8, metadata={"nested": {"tags": ["red"]}}),
            PhotoLayer(self.image("blue"), layer_id="blue", mask=self.mask(255)),
        ]
        return PhotoDocument(
            original_image=self.image("green"), canvas_size=QSize(4, 2), layers=layers,
            composition_rect=QRectF(0.25, 0.5, 3.5, 1.25), source_path="source.jpg",
            metadata={"nested": {"tags": ["document"]}},
        )

    def test_snapshot_pixel_and_mask_changes_do_not_affect_original(self):
        original = self.document()
        snapshot = original.copy()
        for layer_id in ("red", "blue"):
            with self.subTest(layer=layer_id):
                original_layer = original.get_layer(layer_id)
                snapshot_layer = snapshot.get_layer(layer_id)
                self.assertIsNot(original_layer, snapshot_layer)
                expected_color = original_layer.image.pixelColor(0, 0)
                expected_mask = original_layer.mask.pixelColor(0, 0)
                # Изменяем именно внутренние QImage: свойства возвращают копии.
                snapshot_layer._image.setPixelColor(0, 0, QColor("yellow"))
                snapshot_layer._mask.setPixelColor(0, 0, QColor("black"))
                self.assertEqual(original_layer.image.pixelColor(0, 0), expected_color)
                self.assertEqual(original_layer.mask.pixelColor(0, 0), expected_mask)
                self.assertEqual(snapshot_layer.image.pixelColor(0, 0), QColor("yellow"))
                self.assertEqual(snapshot_layer.mask.pixelColor(0, 0).red(), 0)

    def test_original_pixel_and_mask_changes_do_not_affect_snapshot(self):
        original = self.document()
        snapshot = original.copy()
        for layer_id in ("red", "blue"):
            with self.subTest(layer=layer_id):
                original_layer = original.get_layer(layer_id)
                snapshot_layer = snapshot.get_layer(layer_id)
                expected_color = snapshot_layer.image.pixelColor(1, 0)
                expected_mask = snapshot_layer.mask.pixelColor(1, 0)
                original_layer._image.setPixelColor(1, 0, QColor("yellow"))
                original_layer._mask.setPixelColor(1, 0, QColor("black"))
                self.assertEqual(snapshot_layer.image.pixelColor(1, 0), expected_color)
                self.assertEqual(snapshot_layer.mask.pixelColor(1, 0), expected_mask)

    def test_original_images_are_independent_in_both_directions(self):
        original = self.document()
        snapshot = original.copy()
        self.assertIsNot(original._original_image, snapshot._original_image)
        initial = original.original_image.pixelColor(0, 0)
        snapshot._original_image.setPixelColor(0, 0, QColor("yellow"))
        self.assertEqual(original.original_image.pixelColor(0, 0), initial)
        initial_snapshot = snapshot.original_image.pixelColor(1, 0)
        original._original_image.setPixelColor(1, 0, QColor("blue"))
        self.assertEqual(snapshot.original_image.pixelColor(1, 0), initial_snapshot)

    def test_metadata_and_layer_properties_are_independent(self):
        original = self.document()
        snapshot = original.copy()
        snapshot._metadata["nested"]["tags"].append("snapshot")
        original.get_layer("red")._metadata["nested"]["tags"].append("original")
        self.assertEqual(original.metadata, {"nested": {"tags": ["document"]}})
        self.assertEqual(snapshot.get_layer("red").metadata,
                         {"nested": {"tags": ["red"]}})
        snapshot.get_layer("red").visible = False
        snapshot.get_layer("red").opacity = 0.2
        snapshot.remove_layer("blue")
        self.assertTrue(original.get_layer("red").visible)
        self.assertEqual(original.get_layer("red").opacity, 0.8)
        self.assertEqual([layer.layer_id for layer in original.layers], ["red", "blue"])
        self.assertEqual([layer.layer_id for layer in snapshot.layers], ["red"])
        self.assertEqual(snapshot.canvas_size, original.canvas_size)
        self.assertEqual(snapshot.composition_rect, original.composition_rect)
        self.assertEqual(snapshot.source_path, original.source_path)

    def test_getters_return_copies(self):
        document = self.document()
        layer = document.get_layer("red")
        image = layer.image
        mask = layer.mask
        original_image = document.original_image
        metadata = document.metadata
        image.fill(QColor("yellow"))
        mask.fill(0)
        original_image.fill(QColor("yellow"))
        metadata["nested"]["tags"].append("external")
        self.assertEqual(layer.image.pixelColor(0, 0), QColor("red"))
        self.assertEqual(layer.mask.pixelColor(0, 0).red(), 128)
        self.assertEqual(document.original_image.pixelColor(0, 0), QColor("green"))
        self.assertEqual(document.metadata, {"nested": {"tags": ["document"]}})

    def test_numpy_backed_source_and_masks_are_copied(self):
        pixels = np.empty((2, 4, 4), dtype=np.uint8)
        pixels[:] = [11, 22, 33, 255]
        mask_pixels = np.full((2, 4), 128, dtype=np.uint8)
        source = QImage(pixels.data, 4, 2, pixels.strides[0],
                        QImage.Format.Format_RGBA8888)
        mask = QImage(mask_pixels.data, 4, 2, mask_pixels.strides[0],
                      QImage.Format.Format_Grayscale8)
        layer = PhotoLayer(source, layer_id="external", mask=mask)
        document = PhotoDocument(source, canvas_size=QSize(4, 2), layers=[layer])
        snapshot = document.copy()
        pixels[:] = 0
        mask_pixels[:] = 0
        for candidate in (document, snapshot):
            with self.subTest(snapshot=candidate is snapshot):
                color = candidate.get_layer("external").image.pixelColor(0, 0)
                self.assertEqual((color.red(), color.green(), color.blue(), color.alpha()),
                                 (11, 22, 33, 255))
                self.assertEqual(candidate.get_layer("external").mask.pixelColor(0, 0).red(), 128)
                original_color = candidate.original_image.pixelColor(0, 0)
                self.assertEqual((original_color.red(), original_color.green(),
                                  original_color.blue(), original_color.alpha()),
                                 (11, 22, 33, 255))


if __name__ == "__main__":
    unittest.main()
