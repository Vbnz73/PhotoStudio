"""Photo document, layer and non-destructive composite renderer."""

from copy import deepcopy
from typing import Any, Dict, Iterable, Optional
import uuid

import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QImage, QPainter


class PhotoLayer:
    """A same-canvas image layer with independent pixels and grayscale mask."""

    def __init__(self, image: QImage, name: str = "Слой",
                 layer_id: Optional[str] = None, mask: Optional[QImage] = None,
                 visible: bool = True, opacity: float = 1.0,
                 metadata: Optional[Dict[str, Any]] = None):
        if not isinstance(image, QImage) or image.isNull():
            raise ValueError("image must be a non-empty QImage")
        self._image = image.copy()
        self._id = str(layer_id or uuid.uuid4())
        self._name = str(name)
        self._visible = bool(visible)
        self._opacity = self._clamp_opacity(opacity)
        self._metadata = deepcopy(metadata or {})
        self._mask = self._make_mask(mask)

    @staticmethod
    def _clamp_opacity(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def _make_mask(self, mask: Optional[QImage]) -> QImage:
        if mask is None:
            result = QImage(self._image.size(), QImage.Format.Format_Grayscale8)
            result.fill(255)
            return result
        if not isinstance(mask, QImage) or mask.isNull():
            raise ValueError("mask must be None or a non-empty QImage")
        if mask.size() != self._image.size():
            raise ValueError("Layer image and mask must have the same size")
        return mask.convertToFormat(QImage.Format.Format_Grayscale8).copy()

    @property
    def layer_id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = str(value)

    @property
    def image(self) -> QImage:
        return self._image.copy()

    @property
    def mask(self) -> QImage:
        return self._mask.copy()

    @property
    def visible(self) -> bool:
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self._visible = bool(value)

    @property
    def opacity(self) -> float:
        return self._opacity

    @opacity.setter
    def opacity(self, value: float) -> None:
        self._opacity = self._clamp_opacity(value)

    @property
    def metadata(self) -> Dict[str, Any]:
        return deepcopy(self._metadata)

    def set_image(self, image: QImage) -> None:
        if not isinstance(image, QImage) or image.isNull():
            raise ValueError("image must be a non-empty QImage")
        if image.size() != self._mask.size():
            raise ValueError("Replacement image must match the mask size")
        self._image = image.copy()

    def set_mask(self, mask: QImage) -> None:
        self._mask = self._make_mask(mask)

    def copy(self, layer_id: Optional[str] = None) -> "PhotoLayer":
        return PhotoLayer(
            image=self._image.copy(), name=self._name,
            layer_id=layer_id or self._id, mask=self._mask.copy(),
            visible=self._visible, opacity=self._opacity,
            metadata=self._metadata,
        )


class PhotoDocument:
    """One photo's immutable source, canvas geometry and ordered layer stack."""

    def __init__(self, original_image: QImage, canvas_size: Optional[QSize] = None,
                 layers: Optional[Iterable[PhotoLayer]] = None,
                 composition_rect: Optional[QRectF] = None,
                 source_path: Optional[str] = None,
                 metadata: Optional[Dict[str, Any]] = None):
        if not isinstance(original_image, QImage) or original_image.isNull():
            raise ValueError("original_image must be a non-empty QImage")
        self._original_image = original_image.copy()
        input_layers = list(layers or [])
        if canvas_size is None:
            if not input_layers:
                raise ValueError("canvas_size is required for an empty document")
            canvas_size = input_layers[0].image.size()
        if not isinstance(canvas_size, QSize) or canvas_size.width() <= 0 or canvas_size.height() <= 0:
            raise ValueError("canvas_size must have positive dimensions")
        self._canvas_size = QSize(canvas_size)
        self._composition_rect = QRectF(composition_rect or QRectF())
        self._source_path = str(source_path) if source_path is not None else None
        self._metadata = deepcopy(metadata or {})
        self._layers = []
        self._layer_by_id = {}
        for layer in input_layers:
            self.add_layer(layer)

    @classmethod
    def from_working_copy(cls, original_image: QImage, working_image: QImage,
                          composition_rect: Optional[QRectF] = None,
                          source_path: Optional[str] = None) -> "PhotoDocument":
        """Create a one-layer document without changing working-image size."""
        if not isinstance(working_image, QImage) or working_image.isNull():
            raise ValueError("working_image must be a non-empty QImage")
        base_layer = PhotoLayer(
            working_image, name="Рабочее изображение", layer_id="working-image"
        )
        return cls(
            original_image=original_image,
            canvas_size=working_image.size(),
            layers=[base_layer],
            composition_rect=composition_rect,
            source_path=source_path,
        )

    @property
    def original_image(self) -> QImage:
        return self._original_image.copy()

    @property
    def canvas_size(self) -> QSize:
        return QSize(self._canvas_size)

    @property
    def composition_rect(self) -> QRectF:
        return QRectF(self._composition_rect)

    @property
    def source_path(self) -> Optional[str]:
        return self._source_path

    @property
    def metadata(self) -> Dict[str, Any]:
        return deepcopy(self._metadata)

    @property
    def layers(self):
        return tuple(self._layers)

    def get_layer(self, layer_id: str) -> PhotoLayer:
        try:
            return self._layer_by_id[str(layer_id)]
        except KeyError as error:
            raise KeyError(f"Unknown layer id: {layer_id}") from error

    def add_layer(self, layer: PhotoLayer) -> PhotoLayer:
        if not isinstance(layer, PhotoLayer):
            raise TypeError("layer must be a PhotoLayer")
        if layer.layer_id in self._layer_by_id:
            raise ValueError(f"Duplicate layer id: {layer.layer_id}")
        if layer.image.size() != self._canvas_size:
            raise ValueError("Every layer must match the document canvas size")
        stored = layer.copy()
        self._layers.append(stored)
        self._layer_by_id[stored.layer_id] = stored
        return stored

    def remove_layer(self, layer_id: str) -> PhotoLayer:
        layer = self.get_layer(layer_id)
        self._layers.remove(layer)
        del self._layer_by_id[layer.layer_id]
        return layer

    def replace_layer_image(self, layer_id: str, image: QImage) -> None:
        self.get_layer(layer_id).set_image(image)

    def replace_layer_mask(self, layer_id: str, mask: QImage) -> None:
        self.get_layer(layer_id).set_mask(mask)

    def copy(self) -> "PhotoDocument":
        return PhotoDocument(
            original_image=self._original_image.copy(),
            canvas_size=self._canvas_size,
            layers=[layer.copy() for layer in self._layers],
            composition_rect=self._composition_rect,
            source_path=self._source_path,
            metadata=self._metadata,
        )


class CompositeRenderer:
    """Alpha-composite visible layers and masks into a new QImage."""

    @staticmethod
    def _masked_image(image: QImage, mask: QImage) -> QImage:
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        gray = mask.convertToFormat(QImage.Format.Format_Grayscale8)
        width, height = rgba.width(), rgba.height()
        rows = np.frombuffer(
            rgba.constBits(), dtype=np.uint8, count=rgba.sizeInBytes()
        ).reshape(height, rgba.bytesPerLine())
        pixels = rows[:, :width * 4].reshape(height, width, 4).copy()
        mask_rows = np.frombuffer(
            gray.constBits(), dtype=np.uint8, count=gray.sizeInBytes()
        ).reshape(height, gray.bytesPerLine())
        alpha = pixels[:, :, 3].astype(np.uint16)
        mask_values = mask_rows[:, :width].astype(np.uint16)
        pixels[:, :, 3] = ((alpha * mask_values + 127) // 255).astype(np.uint8)
        return QImage(
            pixels.data, width, height, pixels.strides[0],
            QImage.Format.Format_RGBA8888,
        ).copy()

    def render(self, document: PhotoDocument,
               output_size: Optional[QSize] = None) -> QImage:
        if not isinstance(document, PhotoDocument):
            raise TypeError("document must be a PhotoDocument")
        size = QSize(output_size or document.canvas_size)
        if size.width() <= 0 or size.height() <= 0:
            raise ValueError("output_size must have positive dimensions")
        result = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver
        )
        try:
            for layer in document.layers:
                if not layer.visible or layer.opacity <= 0:
                    continue
                image = self._masked_image(layer._image, layer._mask)
                image = image.convertToFormat(
                    QImage.Format.Format_ARGB32_Premultiplied
                )
                if image.size() != size:
                    image = image.scaled(
                        size, Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                painter.setOpacity(layer.opacity)
                painter.drawImage(0, 0, image)
        finally:
            painter.end()
        return result.copy()
