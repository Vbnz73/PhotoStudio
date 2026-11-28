import sys
import os
import ctypes
from ctypes import wintypes
import shutil
import math
import json
import hashlib
import cv2
import mediapipe as mp
import numpy as np
import xml.etree.ElementTree as ET
import copy
import uuid
import concurrent.futures.thread
concurrent.futures.ThreadPoolExecutor = concurrent.futures.thread.ThreadPoolExecutor

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QFileDialog, QListWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QComboBox, QLineEdit, QGroupBox, QDockWidget,
    QToolBar, QMessageBox, QDialog, QFormLayout, QInputDialog,
    QDoubleSpinBox, QDialogButtonBox, QListWidgetItem,
    QTreeView, QFileSystemModel, QListView,
    QTabWidget, QSpinBox, QCheckBox, QTableWidget,
    QTableWidgetItem,
    QButtonGroup,
    QColorDialog,    
    QSlider,
    QAbstractItemView, QMenu,
    QStatusBar,
    QSizePolicy,
)
from PySide6.QtGui import (
    QAction, QPixmap, QImage, QPainter, QPen, QColor, QPainterPath,
    QIcon, QTransform, QImageReader, QImageWriter, QKeySequence, QDrag, QShortcut
)
from PySide6.QtCore import (
    QFile, QSaveFile, QIODevice, QEvent,
    Qt, QPointF, QRectF, QDir, QSize, QSettings, QTimer,
    QPoint, QObject, QRunnable, QThreadPool, Signal, QModelIndex,
    QMimeData, QUrl
)
from thumbnail_provider import get_thumbnail
from photo_grid import PhotoGrid, THUMBNAIL_SIZE
from markup_view import MarkupView
from photo_document import CompositeRenderer, PhotoDocument, PhotoLayer
from photo_export import (
    MIN_DPI, MAX_DPI, calculate_export_size, estimate_print_size_mm,
    prepare_export_image, set_image_dpi,
)
from mask_feather import MaskFeather


BASE_FORMATS = [
    {"name": "Паспорт РФ", "width": 35, "height": 45, "note": ""},
    {"name": "Загранпаспорт", "width": 35, "height": 45, "note": ""},
    {"name": "Анкета на загранпаспорт", "width": 35, "height": 45, "note": ""},
    {"name": "Вид на жительство", "width": 35, "height": 45, "note": ""},
    {"name": "Временная регистрация в Москве", "width": 0, "height": 0, "note": "Размер требует уточнения"},
    {"name": "Разрешение на работу", "width": 30, "height": 40, "note": "Предварительный размер"},
    {"name": "Медицинская книжка", "width": 30, "height": 40, "note": "Предварительный размер"},
    {"name": "Пенсионное удостоверение", "width": 21, "height": 30, "note": "Для новой модели; уточнить"},
    {"name": "Студенческий билет", "width": 30, "height": 40, "note": "Предварительный размер"},
    {"name": "Ветеран войны", "width": 30, "height": 40, "note": "Предварительный размер"},
    {"name": "Военный билет", "width": 30, "height": 40, "note": "Предварительный размер"},
    {"name": "Паспорт моряка", "width": 0, "height": 0, "note": "Размер требует уточнения"},
    {"name": "Водительское удостоверение", "width": 35, "height": 45, "note": ""},
    {"name": "Мать-одиночка", "width": 0, "height": 0, "note": "Размер требует уточнения"},
    {"name": "Пропуск 6×9", "width": 60, "height": 90, "note": ""},
    {"name": "3×4 с уголком слева", "width": 30, "height": 40, "note": ""},
    {"name": "3×4 с уголком справа", "width": 30, "height": 40, "note": ""},
    {"name": "3×4 цветная", "width": 30, "height": 40, "note": ""},
    {"name": "3,5×4,5 цветная", "width": 35, "height": 45, "note": ""},
    {"name": "4×6 чёрно-белая", "width": 40, "height": 60, "note": ""},
    {"name": "Green Card / США", "width": 51, "height": 51, "note": ""},
    {"name": "Шенгенская виза", "width": 35, "height": 45, "note": ""},
    {"name": "Финляндия", "width": 36, "height": 47, "note": "Серый фон"},
    {"name": "Китай", "width": 33, "height": 48, "note": ""},
    {"name": "Канада — виза", "width": 35, "height": 45, "note": ""},
    {"name": "Канада — паспорт", "width": 50, "height": 70, "note": ""},
    {"name": "Англия, ОАЭ, Болгария", "width": 0, "height": 0, "note": "Размер требует уточнения"},
]


# ---------------------------------------------------------
# НАСТРОЙКИ БЫСТРОГО ПРОСМОТРА ФОТОГРАФИЙ
# ---------------------------------------------------------

# Размер миниатюры в сетке.


# Количество одновременно работающих потоков загрузки.
WORKERS = 12

# Размер одной ячейки сетки фотографий.
ITEM_WIDTH = 90
ITEM_HEIGHT = 95

# Поддерживаемые форматы фотографий.
IMAGE_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".bmp",
    ".webp", ".tif", ".tiff"
)

EXPORT_PNG_FILTER = "PNG (*.png)"
EXPORT_JPEG_FILTER = "JPEG (*.jpg *.jpeg)"


# ---------------------------------------------------------
# СЕТКА ФОТОГРАФИЙ С ПОДДЕРЖКОЙ ПЕРЕТАСКИВАНИЯ
# ---------------------------------------------------------


# ---------------------------------------------------------
# СИГНАЛЫ ДЛЯ ПЕРЕДАЧИ ГОТОВЫХ МИНИАТЮР
# ---------------------------------------------------------

class ThumbnailSignals(QObject):
    # Передаём путь к файлу и готовую миниатюру.
    finished = Signal(str, object)


# ---------------------------------------------------------
# ЗАДАЧА ЗАГРУЗКИ ОДНОЙ МИНИАТЮРЫ
# ---------------------------------------------------------

class ThumbnailTask(QRunnable):

    def __init__(self, path, signals):
        super().__init__()

        self.path = path
        self.signals = signals

    def run(self):
        # Получаем миниатюру через Windows Shell.
        image = get_thumbnail(
            self.path,
            THUMBNAIL_SIZE,
            THUMBNAIL_SIZE
        )

        # Если Shell не смог получить изображение,
        # пробуем прочитать его обычным способом.
        if image is None or image.isNull():
            reader = QImageReader(self.path)
            reader.setAutoTransform(True)
            image = reader.read()

            if not image.isNull():
                image = image.scaled(
                    THUMBNAIL_SIZE,
                    THUMBNAIL_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )

        # Передаём результат главному окну.
        self.signals.finished.emit(self.path, image)

# ---------------------------------------------------------
# МОДЕЛЬ ДЕРЕВА ПАПОК
# ---------------------------------------------------------

class FolderOnlyFileSystemModel(QFileSystemModel):
    """Показывает раскрытие только у папок с вложенными папками."""

    def hasChildren(self, parent=QModelIndex()):
        # Корневой индекс и пустые индексы обрабатывает стандартная модель.
        if not parent.isValid():
            return super().hasChildren(parent)

        # Если содержимое ещё не загружено, сначала загружаем его.
        if self.canFetchMore(parent):
            self.fetchMore(parent)

        # После загрузки проверяем наличие вложенных папок.
        for row in range(self.rowCount(parent)):
            child_index = self.index(row, 0, parent)

            if self.isDir(child_index):
                return True

        # Подпапок нет — треугольник не показываем.
        return False

# ---------------------------------------------------------
# ДЕРЕВО ПАПОК С ПОДДЕРЖКОЙ ПЕРЕМЕЩЕНИЯ ФОТОГРАФИЙ
# ---------------------------------------------------------

class PhotoFolderTree(QTreeView):

    def __init__(self, move_callback, parent=None):
        super().__init__(parent)

        # Сохраняем функцию перемещения файлов.
        self.move_callback = move_callback

    def dragEnterEvent(self, event):
        # Принимаем только перетаскивание файлов.
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        # Разрешаем перетаскивание над папками.
        index = self.indexAt(event.position().toPoint())

        if index.isValid() and self.model().isDir(index):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        # Определяем папку, на которую бросили фотографии.
        index = self.indexAt(event.position().toPoint())

        if not index.isValid():
            event.ignore()
            return

        if not self.model().isDir(index):
            event.ignore()
            return

        destination_folder = self.model().filePath(index)

        # Получаем пути перетаскиваемых файлов.
        source_paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]

        if not source_paths:
            event.ignore()
            return

        # Передаём файлы главному окну для перемещения.
        self.move_callback(
            source_paths,
            destination_folder
        )

        event.acceptProposedAction()

class FormatDialog(QDialog):
    def __init__(self, parent=None, data=None):
        super().__init__(parent)

        self.setWindowTitle("Параметры формата")
        self.setMinimumWidth(320)

        data = data or {}
        self.original_data = dict(data)
        self.units = data.get("units", "mm")

        unit_suffixes = {
            "mm": " мм",
            "in": " ″",
            "px": " px"
        }
        unit_suffix = unit_suffixes.get(self.units, " ед.")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.markup_combo = QComboBox()
        self.markup_combo.addItems([
            "Разметка по двум точкам",
            "Разметка по трём точкам (ГОСТ)"
        ])

        saved_markup = data.get("markup_type", "Разметка по двум точкам")
        if saved_markup not in [
            "Разметка по двум точкам",
            "Разметка по трём точкам (ГОСТ)",
            "Как Бог на душу положит"
        ]:
            saved_markup = "Разметка по двум точкам"

        self.markup_combo.setCurrentText(saved_markup)

        self.name_edit = QLineEdit(data.get("name", ""))

        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0, 1000)
        self.width_spin.setDecimals(1)
        self.width_spin.setSuffix(unit_suffix)
        
        width_value = data.get("width", 35)
        try:
            width_value = float(width_value) if width_value else 0.0
        except (TypeError, ValueError):
            width_value = 35.0
        self.width_spin.setValue(width_value)

        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(0, 1000)
        self.height_spin.setDecimals(1)
        self.height_spin.setSuffix(unit_suffix)

        height_value = data.get("height", 45)
        try:
            height_value = float(height_value) if height_value else 0.0
        except (TypeError, ValueError):
            height_value = 45.0
        self.height_spin.setValue(height_value)
        self.top_margin_spin = QDoubleSpinBox()
        self.top_margin_spin.setRange(0, 999999.9)
        self.top_margin_spin.setDecimals(1)
        self.top_margin_spin.setSingleStep(0.1)
        self.top_margin_spin.setSuffix(unit_suffix)
        self.top_margin_spin.setValue(float(data.get("top_margin", 3.0) or 0))

        self.head_size_spin = QDoubleSpinBox()
        self.head_size_spin.setRange(0, 999999.9)
        self.head_size_spin.setDecimals(1)
        self.head_size_spin.setSingleStep(0.1)
        self.head_size_spin.setSuffix(unit_suffix)
        head_value = data.get("head_size")

        if head_value is None or head_value == "":
            try:
                head_value = (
                    self.height_spin.value()
                    - self.top_margin_spin.value()
                    - float(data.get("bottom_margin", 0) or 0)
                )
            except (TypeError, ValueError):
                head_value = 0

        try:
            head_value = float(head_value)
        except (TypeError, ValueError):
            head_value = 0

        self.head_size_spin.setValue(max(0, head_value))

        self.face_size_spin = QDoubleSpinBox()
        self.face_size_spin.setRange(0, 999999.9)
        self.face_size_spin.setDecimals(1)
        self.face_size_spin.setSingleStep(0.1)
        self.face_size_spin.setSuffix(unit_suffix)
        self.face_size_spin.setValue(
            float(data.get("face_size", data.get("front_size", 0)) or 0)
        )

        self.bottom_margin_spin = QDoubleSpinBox()
        self.bottom_margin_spin.setRange(0, 999999.9)
        self.bottom_margin_spin.setDecimals(1)
        self.bottom_margin_spin.setSingleStep(0.1)
        self.bottom_margin_spin.setSuffix(unit_suffix)
        self.bottom_margin_spin.setValue(float(data.get("bottom_margin", 3.0) or 0))

        self.note_edit = QLineEdit(data.get("note", ""))

        self.head_size_spin.setMaximum(
            max(0, self.height_spin.value() - self.top_margin_spin.value())
        )
        self.recalculate_bottom_margin()

        self.height_spin.valueChanged.connect(self.recalculate_head_size)
        self.top_margin_spin.valueChanged.connect(self.recalculate_head_size)
        self.bottom_margin_spin.valueChanged.connect(self.recalculate_head_size)
        self.head_size_spin.valueChanged.connect(self.recalculate_bottom_margin)

        self.markup_combo.currentTextChanged.connect(self.update_markup_fields)
        self.update_markup_fields()
        
        form.addRow("Тип разметки:", self.markup_combo)        
        form.addRow("Название:", self.name_edit)
        form.addRow("Ширина:", self.width_spin)
        form.addRow("Высота:", self.height_spin)
        form.addRow("Верхнее поле:", self.top_margin_spin)
        form.addRow("Размер головы:", self.head_size_spin)
        form.addRow("Лицевая часть:", self.face_size_spin)
        form.addRow("Нижнее поле:", self.bottom_margin_spin)
        form.addRow("Примечание:", self.note_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def update_markup_fields(self, _text=None):
        markup_type = self.markup_combo.currentText()

        if markup_type == "Разметка по двум точкам":
            self.top_margin_spin.setEnabled(True)
            self.bottom_margin_spin.setEnabled(True)
            self.face_size_spin.setEnabled(False)
            self.head_size_spin.setEnabled(True)

        elif markup_type == "Разметка по трём точкам (ГОСТ)":
            self.top_margin_spin.setEnabled(True)
            self.bottom_margin_spin.setEnabled(False)
            self.face_size_spin.setEnabled(True)
            self.head_size_spin.setEnabled(False)

        else:
            self.top_margin_spin.setEnabled(True)
            self.bottom_margin_spin.setEnabled(True)
            self.face_size_spin.setEnabled(True)
            self.head_size_spin.setEnabled(True)

    def recalculate_head_size(self, _value=None):
        if self.markup_combo.currentText() != "Разметка по двум точкам":
            return

        height = self.height_spin.value()
        top = self.top_margin_spin.value()
        bottom = self.bottom_margin_spin.value()

        new_head = max(0, height - top - bottom)

        self.head_size_spin.blockSignals(True)
        self.head_size_spin.setValue(new_head)
        self.head_size_spin.blockSignals(False)

    def recalculate_bottom_margin(self, _value=None):
        if self.markup_combo.currentText() != "Разметка по двум точкам":
            return

        height = self.height_spin.value()
        top = self.top_margin_spin.value()
        head = self.head_size_spin.value()

        new_bottom = max(0, height - top - head)

        self.bottom_margin_spin.blockSignals(True)
        self.bottom_margin_spin.setValue(new_bottom)
        self.bottom_margin_spin.blockSignals(False)

    def get_data(self):
        result = dict(self.original_data)
        result.update({
            "name": self.name_edit.text().strip(),
            "width": self.width_spin.value(),
            "height": self.height_spin.value(),
            "markup_type": self.markup_combo.currentText(),
            "top_margin": self.top_margin_spin.value(),
            "head_size": self.head_size_spin.value(),
            "face_size": self.face_size_spin.value(),
            "bottom_margin": self.bottom_margin_spin.value(),
            "note": self.note_edit.text().strip(),
        })
        return result


_face_landmarker = None



def detect_head_top_on_white_background(
    image_data, face_rect, left_eye, right_eye, nose_bridge
):
    height, width = image_data.shape[:2]

    # 1. Берём синий канал RGB-изображения.
    blue = image_data[:, :, 2]

    # 2. Сглаживаем небольшие шумы.
    blurred = cv2.GaussianBlur(blue, (5, 5), 0)

    # 3. Отделяем человека от белого фона методом Otsu.
    threshold_value = 180

    _, mask = cv2.threshold(
        blurred,
        threshold_value,
        255,
        cv2.THRESH_BINARY_INV
    )

    print("Порог Otsu:", threshold_value)

    # 4. Убираем мелкий шум и соединяем небольшие разрывы.
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        np.ones((3, 3), np.uint8)
    )

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        np.ones((3, 3), np.uint8)
    )

    # Временная диагностическая функция.
    # Она только сохраняет изображение и не меняет алгоритм.
    def save_debug(
        selected=0,
        labels=None,
        seed_point=None,
        head_point=None
    ):
        import os

        original = cv2.cvtColor(
            image_data.copy(),
            cv2.COLOR_RGB2BGR
        )

        panel_original = original.copy()
        panel_result = original.copy()

        panel_mask = cv2.cvtColor(
            mask,
            cv2.COLOR_GRAY2BGR
        )

        eye_left = (
            int(round(left_eye.x())),
            int(round(left_eye.y()))
        )

        eye_right = (
            int(round(right_eye.x())),
            int(round(right_eye.y()))
        )

        bridge = (
            int(round(nose_bridge.x())),
            int(round(nose_bridge.y()))
        )

        midpoint_x = (left_eye.x() + right_eye.x()) / 2
        midpoint_y = (left_eye.y() + right_eye.y()) / 2

        eye_dx = right_eye.x() - left_eye.x()
        eye_dy = right_eye.y() - left_eye.y()
        eye_distance = math.hypot(eye_dx, eye_dy)

        if eye_distance >= 10:
            up_x = eye_dy / eye_distance
            up_y = -eye_dx / eye_distance

            side_x = -up_y
            side_y = up_x

            corridor = eye_distance * 0.35
            max_distance = face_rect.height() * 1.5

            # Центральная линия через середину глаз.
            axis_start = (
                int(round(midpoint_x)),
                int(round(midpoint_y))
            )

            axis_end = (
                int(round(midpoint_x + up_x * max_distance)),
                int(round(midpoint_y + up_y * max_distance))
            )

            # Текущий коридор алгоритма начинается от переносицы.
            start_distance = eye_distance * 0.15

            p1 = (
                int(round(
                    nose_bridge.x()
                    + up_x * start_distance
                    + side_x * corridor
                )),
                int(round(
                    nose_bridge.y()
                    + up_y * start_distance
                    + side_y * corridor
                ))
            )

            p2 = (
                int(round(
                    nose_bridge.x()
                    + up_x * start_distance
                    - side_x * corridor
                )),
                int(round(
                    nose_bridge.y()
                    + up_y * start_distance
                    - side_y * corridor
                ))
            )

            p3 = (
                int(round(
                    nose_bridge.x()
                    + up_x * max_distance
                    - side_x * corridor
                )),
                int(round(
                    nose_bridge.y()
                    + up_y * max_distance
                    - side_y * corridor
                ))
            )

            p4 = (
                int(round(
                    nose_bridge.x()
                    + up_x * max_distance
                    + side_x * corridor
                )),
                int(round(
                    nose_bridge.y()
                    + up_y * max_distance
                    + side_y * corridor
                ))
            )

            polygon = np.array(
                [p1, p2, p3, p4],
                dtype=np.int32
            )

            # Синяя линия — перпендикуляр через середину глаз.
            cv2.line(
                panel_original,
                axis_start,
                axis_end,
                (255, 0, 0),
                2
            )

            cv2.line(
                panel_result,
                axis_start,
                axis_end,
                (255, 0, 0),
                2
            )

            # Зелёный контур — фактический коридор поиска.
            cv2.polylines(
                panel_original,
                [polygon],
                True,
                (0, 255, 0),
                2
            )

            cv2.polylines(
                panel_result,
                [polygon],
                True,
                (0, 255, 0),
                2
            )

        # Жёлтая линия — линия глаз.
        cv2.line(
            panel_original,
            eye_left,
            eye_right,
            (0, 255, 255),
            2
        )

        cv2.line(
            panel_result,
            eye_left,
            eye_right,
            (0, 255, 255),
            2
        )

        # Отмечаем глаза.
        cv2.circle(panel_original, eye_left, 5, (0, 255, 255), -1)
        cv2.circle(panel_original, eye_right, 5, (0, 255, 255), -1)

        cv2.circle(panel_result, eye_left, 5, (0, 255, 255), -1)
        cv2.circle(panel_result, eye_right, 5, (0, 255, 255), -1)

        # Розовая точка — переносица.
        cv2.circle(panel_original, bridge, 6, (255, 0, 255), -1)
        cv2.circle(panel_result, bridge, 6, (255, 0, 255), -1)

        # Оранжевая точка — контрольная точка выбора компонента.
        if seed_point is not None:
            seed = (
                int(round(seed_point[0])),
                int(round(seed_point[1]))
            )

            cv2.circle(
                panel_original,
                seed,
                6,
                (0, 165, 255),
                2
            )

            cv2.circle(
                panel_result,
                seed,
                6,
                (0, 165, 255),
                2
            )

        # Выделяем выбранный компонент силуэта.
        if labels is not None and selected > 0:
            component = labels == selected

            panel_mask[component] = (0, 180, 255)

        # Красная точка — найденная макушка.
        if head_point is not None:
            head = (
                int(round(head_point[0])),
                int(round(head_point[1]))
            )

            cv2.circle(
                panel_result,
                head,
                9,
                (0, 0, 255),
                3
            )

            cv2.drawMarker(
                panel_result,
                head,
                (0, 0, 255),
                cv2.MARKER_CROSS,
                20,
                2
            )

        # Подписи панелей.
        cv2.putText(
            panel_original,
            "GEOMETRY",
            (15, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2
        )

        cv2.putText(
            panel_mask,
            "MASK / COMPONENT",
            (15, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 0, 255),
            2
        )

        cv2.putText(
            panel_result,
            "DETECTED HEAD TOP",
            (15, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2
        )

        # Объединяем три изображения рядом.
        debug_image = np.hstack((
            panel_original,
            panel_mask,
            panel_result
        ))

        # Если изображение слишком широкое — уменьшаем его.
        if debug_image.shape[1] > 2400:
            scale = 2400 / debug_image.shape[1]

            debug_image = cv2.resize(
                debug_image,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_AREA
            )

        debug_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "head_top_debug.png"
        )

        try:
            cv2.imwrite(debug_path, debug_image)
        except Exception:
            pass

    # 5. Проверяем расстояние между глазами.
    eye_dx = right_eye.x() - left_eye.x()
    eye_dy = right_eye.y() - left_eye.y()

    eye_distance = math.hypot(eye_dx, eye_dy)

    if eye_distance < 10:
        save_debug()
        return None

    # 6. Строим единичный вектор перпендикулярно линии глаз.
    up_x = eye_dy / eye_distance
    up_y = -eye_dx / eye_distance

    side_x = -up_y
    side_y = up_x

    # 7. Выбираем компонент силуэта в области лица.
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask,
        connectivity=8
    )

    seed_x = int(round(nose_bridge.x()))
    seed_y = int(round(nose_bridge.y() - up_y * eye_distance * 0.25))

    seed_x = max(0, min(width - 1, seed_x))
    seed_y = max(0, min(height - 1, seed_y))

    selected = int(labels[seed_y, seed_x])

    # Если контрольная точка попала на разрыв маски,
    # ищем ближайший компонент в небольшой области.
    if selected == 0:
        radius = max(3, int(eye_distance * 0.25))

        x1 = max(0, seed_x - radius)
        x2 = min(width, seed_x + radius + 1)
        y1 = max(0, seed_y - radius)
        y2 = min(height, seed_y + radius + 1)

        nearby = labels[y1:y2, x1:x2]

        values, counts = np.unique(
            nearby[nearby > 0],
            return_counts=True
        )

        if len(values):
            selected = int(values[np.argmax(counts)])

    if selected == 0:
        save_debug(
            selected=selected,
            labels=labels,
            seed_point=(seed_x, seed_y)
        )
        return None

    # 8. Получаем координаты пикселей выбранного силуэта.
    component_y, component_x = np.where(labels == selected)

    # 9. Рассчитываем положение каждого пикселя.
    rel_x = component_x.astype(np.float32) - nose_bridge.x()
    rel_y = component_y.astype(np.float32) - nose_bridge.y()

    distance_up = rel_x * up_x + rel_y * up_y
    distance_side = rel_x * side_x + rel_y * side_y

    # 10. Ограничиваем область поиска коридором.
    face_height = face_rect.height()

    if face_height <= 0:
        save_debug(
            selected=selected,
            labels=labels,
            seed_point=(seed_x, seed_y)
        )
        return None

    corridor = eye_distance * 0.35
    max_distance = face_height * 1.5

    region = (
        (distance_up > eye_distance * 0.15) &
        (distance_up < max_distance) &
        (np.abs(distance_side) <= corridor)
    )

    if not np.any(region):
        save_debug(
            selected=selected,
            labels=labels,
            seed_point=(seed_x, seed_y)
        )
        return None

    # 11. Находим самую верхнюю часть силуэта в коридоре.
    candidate_x = component_x[region]
    candidate_y = component_y[region]
    candidate_distance = distance_up[region]

    top_distance = float(np.max(candidate_distance))

    top_band = max(2.0, eye_distance * 0.06)

    top_indices = candidate_distance >= top_distance - top_band

    top_x = float(np.median(candidate_x[top_indices]))
    top_y = float(np.median(candidate_y[top_indices]))

    # 12. Дополнительная проверка положения макушки.
    if top_y >= nose_bridge.y():
        save_debug(
            selected=selected,
            labels=labels,
            seed_point=(seed_x, seed_y),
            head_point=(top_x, top_y)
        )
        return None

    if top_distance < eye_distance * 0.2:
        save_debug(
            selected=selected,
            labels=labels,
            seed_point=(seed_x, seed_y),
            head_point=(top_x, top_y)
        )
        return None

    if top_distance > max_distance:
        save_debug(
            selected=selected,
            labels=labels,
            seed_point=(seed_x, seed_y),
            head_point=(top_x, top_y)
        )
        return None

    # Сохраняем диагностику успешного определения.
    save_debug(
        selected=selected,
        labels=labels,
        seed_point=(seed_x, seed_y),
        head_point=(top_x, top_y)
    )

    return QPointF(top_x, top_y)


def detect_face_and_eyes(pixmap):
    global _face_landmarker

    if _face_landmarker is None:
        model_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "face_landmarker.task"
        )
        if not os.path.isfile(model_path):
            raise FileNotFoundError(
                f"Не найдена модель распознавания лица:\n{model_path}\n\n"
                "Файл face_landmarker.task должен находиться рядом с main.py."
            )
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(
                model_asset_path=model_path
            ),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_faces=1
        )
        try:
            _face_landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)
        except Exception as e:
            raise RuntimeError(f"Ошибка инициализации модели: {e}")

    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)
    width = image.width()
    height = image.height()
    stride = image.bytesPerLine()
    ptr = image.bits()
    data = np.frombuffer(ptr, dtype=np.uint8)
    data = data.reshape((height, stride))
    data = data[:, :width * 3]
    data = data.reshape((height, width, 3))
    data = np.ascontiguousarray(data)

    mp_image = mp.Image(
        image_format=mp.ImageFormat.SRGB,
        data=data
    )
    result = _face_landmarker.detect(mp_image)

    if not result.face_landmarks:
        return None

    landmarks = result.face_landmarks[0]
    points = [
        QPointF(point.x * width, point.y * height)
        for point in landmarks
    ]

    min_x = min(point.x() for point in points)
    max_x = max(point.x() for point in points)
    min_y = min(point.y() for point in points)
    max_y = max(point.y() for point in points)

    face_rect = QRectF(
        min_x, min_y,
        max_x - min_x,
        max_y - min_y
    )

    def safe_iris_center(indices):
        valid = [i for i in indices if i < len(points)]
        if not valid:
            return None
        return QPointF(
            sum(points[i].x() for i in valid) / len(valid),
            sum(points[i].y() for i in valid) / len(valid)
        )

    eye1 = safe_iris_center(range(468, 473))
    eye2 = safe_iris_center(range(473, 478))

    if eye1 is None:
        if len(points) > 133 and len(points) > 33:
            eye1 = QPointF((points[33].x() + points[133].x()) / 2, (points[33].y() + points[133].y()) / 2)
    if eye2 is None:
        if len(points) > 362 and len(points) > 263:
            eye2 = QPointF((points[362].x() + points[263].x()) / 2, (points[362].y() + points[263].y()) / 2)

    if eye1 is None or eye2 is None:
        return None

    eyes = sorted([eye1, eye2], key=lambda p: p.x())
    left_eye = eyes[0]
    right_eye = eyes[1]

    chin = points[152] if len(points) > 152 else None
    nose_bridge = points[168] if len(points) > 168 else None
    eye_center = QPointF(
        (left_eye.x() + right_eye.x()) / 2,
        (left_eye.y() + right_eye.y()) / 2
    )

    if chin is not None:
        # Линия между глазами
        eye_dx = right_eye.x() - left_eye.x()
        eye_dy = right_eye.y() - left_eye.y()

        eye_distance = math.hypot(eye_dx, eye_dy)

        if eye_distance > 1:
            # Строго 90 градусов к линии глаз
            perp_x = -eye_dy / eye_distance
            perp_y = eye_dx / eye_distance

            # Направляем перпендикуляр от глаз ВВЕРХ,
            # то есть в сторону, противоположную подбородку
            chin_dx = chin.x() - eye_center.x()
            chin_dy = chin.y() - eye_center.y()

            if perp_x * chin_dx + perp_y * chin_dy > 0:
                perp_x = -perp_x
                perp_y = -perp_y

            # Область поиска макушки.
            # Ищем только в узком коридоре вокруг
            # перпендикуляра от центра глаз.
            search_half_width = eye_distance * 0.45
            search_length = eye_distance * 2.5

            # Точка, до которой разрешён поиск
            search_end = QPointF(
                eye_center.x() + perp_x * search_length,
                eye_center.y() + perp_y * search_length
            )

            # Ограничиваем область поиска координатами изображения
            search_x_min = max(
                0.0,
                min(eye_center.x(), search_end.x()) - search_half_width
            )
            search_x_max = min(
                float(width),
                max(eye_center.x(), search_end.x()) + search_half_width
            )
            search_y_min = max(
                0.0,
                min(eye_center.y(), search_end.y()) - search_half_width
            )
            search_y_max = min(
                float(height),
                max(eye_center.y(), search_end.y()) + search_half_width
            )

            # Сначала берём приблизительную макушку
            # по геометрии: от центра глаз по перпендикуляру.
            head_top = search_end

            # Проверяем, что точка действительно находится
            # выше глаз и в пределах фотографии.
            if (
                head_top.y() >= eye_center.y()
                or head_top.x() < search_x_min
                or head_top.x() > search_x_max
                or head_top.y() < search_y_min
                or head_top.y() > search_y_max
            ):
                head_top = QPointF(
                    eye_center.x() + perp_x * eye_distance * 1.8,
                    eye_center.y() + perp_y * eye_distance * 1.8
                )
        else:
            head_top = None
    else:
        head_top = None

    # Пытаемся определить настоящую макушку по силуэту.
    if nose_bridge is not None:
        try:
            silhouette_head_top = detect_head_top_on_white_background(
                data,
                face_rect,
                left_eye,
                right_eye,
                nose_bridge
            )

            if silhouette_head_top is not None:
                head_top = silhouette_head_top

        except Exception as e:
            print(f"Ошибка определения макушки: {e}")

    return face_rect, left_eye, right_eye, head_top, chin


class FormatPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.pixmap = QPixmap()
        self.aspect_ratio = 35 / 45

        self.head_top = None
        self.chin = None
        self.left_eye = None
        self.right_eye = None

        self.top_margin = 5
        self.head_size = 30
        self.face_size = 9
        self.markup_type = "Разметка по двум точкам"
        self.format_height = 45
        self.black_white = False

        # Параметры уголка для отображения в образце.
        self.corner_enabled = False
        self.corner_width = 0
        self.corner_height = 0
        self.corner_type = 0
        self.corner_shape = "Прямой срез"

        self.setMinimumSize(180, 230)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )

    def set_photo(self, pixmap):
        self.pixmap = pixmap
        self.update()

    def set_format(
        self,
        width,
        height,
        top_margin,
        head_size,
        face_size,
        markup_type
    ):
        if width > 0 and height > 0:
            self.aspect_ratio = width / height
            self.format_height = height

        self.top_margin = top_margin
        self.head_size = head_size
        self.face_size = face_size
        self.markup_type = markup_type

        self.update()

    def set_landmarks(self, head_top, chin, left_eye, right_eye):
        self.head_top = head_top
        self.chin = chin
        self.left_eye = left_eye
        self.right_eye = right_eye
        self.update()

    def set_corner(self, enabled, width, height, corner_type, shape):
        # Получаем настройки уголка из окна «Форматы».
        self.corner_enabled = enabled
        self.corner_width = width
        self.corner_height = height
        self.corner_type = corner_type
        self.corner_shape = shape

        # Перерисовываем образец с новыми параметрами.
        self.update()

    def set_black_white(self, enabled):
        self.black_white = enabled
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#444444"))

        if self.pixmap.isNull():
            return

        image = self.pixmap.toImage()

        if self.black_white:
            image = image.convertToFormat(
                QImage.Format.Format_Grayscale8
            )

        source = QRectF(
            0, 0,
            image.width(),
            image.height()
        )

        if (
            self.head_top is not None
            and self.chin is not None
            and self.head_top.y() < self.chin.y()
            and self.head_size > 0
            and self.top_margin >= 0
        ):
            head_height = self.chin.y() - self.head_top.y()

            if self.markup_type == "Разметка по трём точкам (ГОСТ)":
                eye_line_y = (
                    self.left_eye.y() + self.right_eye.y()
                ) / 2 if (
                    self.left_eye is not None
                    and self.right_eye is not None
                ) else self.head_top.y() + head_height * 0.48

                face_height = self.chin.y() - eye_line_y
                reference_size = self.face_size

                if face_height <= 0:
                    return

                crop_height = (
                    face_height
                    * self.format_height
                    / reference_size
                )
            else:
                reference_size = self.head_size

                if reference_size <= 0:
                    return

                crop_height = (
                    head_height
                    * self.format_height
                    / reference_size
                )

            if self.format_height <= 0:
                return

            crop_width = crop_height * self.aspect_ratio

            eye_center_x = (
                (self.left_eye.x() + self.right_eye.x()) / 2
                if self.left_eye is not None
                and self.right_eye is not None
                else self.head_top.x()
            )

            crop_x = eye_center_x - crop_width / 2

            crop_y = self.head_top.y() - (
                crop_height * self.top_margin
                / self.format_height
            )

            source = QRectF(
                crop_x,
                crop_y,
                crop_width,
                crop_height
            )


        if source.isEmpty():
            return

        # Сохраняем пропорции кадра без растягивания фотографии.
        if source.width() / source.height() > self.aspect_ratio:
            new_width = source.height() * self.aspect_ratio
            source.setX(source.center().x() - new_width / 2)
            source.setWidth(new_width)
        else:
            new_height = source.width() / self.aspect_ratio
            source.setY(source.center().y() - new_height / 2)
            source.setHeight(new_height)

        # Если кадр больше исходной фотографии, уменьшаем его целиком.
        scale = min(
            1.0,
            image.width() / source.width(),
            image.height() / source.height()
        )

        if scale < 1.0:
            new_width = source.width() * scale
            new_height = source.height() * scale
            source.setSize(QSize(
                round(new_width),
                round(new_height)
            ))

        # Перемещаем кадр внутрь фотографии, не меняя его пропорции.
        source.setX(max(0, min(
            source.x(),
            image.width() - source.width()
        )))
        source.setY(max(0, min(
            source.y(),
            image.height() - source.height()
        )))


        target = QRectF(self.rect())

        if target.width() / target.height() > self.aspect_ratio:
            target_width = target.height() * self.aspect_ratio
            target.setX(
                target.x() + (target.width() - target_width) / 2
            )
            target.setWidth(target_width)
        else:
            target_height = target.width() / self.aspect_ratio
            target.setY(
                target.y() + (target.height() - target_height) / 2
            )
            target.setHeight(target_height)

        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, image, source)

        # -------------------------------------------------
        # ОТОБРАЖЕНИЕ УГОЛКА НА ОБРАЗЦЕ ФОТОГРАФИИ
        # -------------------------------------------------

        if (
            self.corner_enabled
            and self.corner_type in (4, 5)
            and self.corner_width > 0
            and self.corner_height > 0
            and self.format_height > 0
        ):
            # Переводим размеры уголка из единиц формата в пиксели.
            format_width = self.format_height * self.aspect_ratio

            corner_w = (
                target.width()
                * self.corner_width
                / format_width
            )

            corner_h = (
                target.height()
                * self.corner_height
                / self.format_height
            )


            # Выбираем левый или правый НИЖНИЙ угол фотографии.
            if self.corner_type == 4:
                corner_x = target.left()
            else:
                corner_x = target.right()

            corner_y = target.bottom()

            # Белая заливка срезанного участка.
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFFFFF"))

            corner_path = QPainterPath()

            if self.corner_shape == "Закруглённый срез":

                # Границы окружности с центром в нижнем углу фотографии.
                arc_rect = QRectF(
                    corner_x - corner_w,
                    corner_y - corner_h,
                    corner_w * 2,
                    corner_h * 2
                )

                if self.corner_type == 4:
                    # Левый нижний угол.
                    # Центр окружности — левый нижний угол фотографии.
                    corner_path.moveTo(corner_x, corner_y)
                    corner_path.lineTo(
                        corner_x + corner_w,
                        corner_y
                    )
                    corner_path.arcTo(
                        arc_rect,
                        0,
                        90
                    )

                else:
                    # Правый нижний угол.
                    # Центр окружности — правый нижний угол фотографии.
                    corner_path.moveTo(corner_x, corner_y)
                    corner_path.lineTo(
                        corner_x,
                        corner_y - corner_h
                    )
                    corner_path.arcTo(
                        arc_rect,
                        90,
                        90
                    )

            else:
                # Прямой срез — равнобедренный прямоугольный треугольник.
                if self.corner_type == 4:
                    # Левый нижний угол.
                    corner_path.moveTo(corner_x, corner_y)
                    corner_path.lineTo(
                        corner_x + corner_w,
                        corner_y
                    )
                    corner_path.lineTo(
                        corner_x,
                        corner_y - corner_h
                    )

                else:
                    # Правый нижний угол.
                    corner_path.moveTo(corner_x, corner_y)
                    corner_path.lineTo(
                        corner_x - corner_w,
                        corner_y
                    )
                    corner_path.lineTo(
                        corner_x,
                        corner_y - corner_h
                    )

            corner_path.closeSubpath()
            painter.drawPath(corner_path)
            painter.restore()



class FormatSettingsDialog(QDialog):
    def __init__(self, formats, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Форматы")
        self.resize(1100, 700)
        self.formats = copy.deepcopy(formats)
        self.current_index = -1

        main_layout = QVBoxLayout(self)
        columns = QHBoxLayout()
        main_layout.addLayout(columns)

        left_panel = QVBoxLayout()
        columns.addLayout(left_panel, 2)
        left_panel.addWidget(QLabel("Каталог форматов:"))
        self.country_combo = QComboBox()
        self.country_combo.addItems([
            "Базовые форматы",
            "Мои форматы",
            "Все форматы"
        ])
        self.country_combo.setCurrentText("Все форматы")
        left_panel.addWidget(self.country_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск формата...")
        left_panel.addWidget(self.search_edit)

        self.format_list = QListWidget()
        left_panel.addWidget(self.format_list)

        catalog_buttons = QHBoxLayout()
        add_button = QPushButton("Добавить")
        duplicate_button = QPushButton("Дублировать")
        delete_button = QPushButton("✖")
        catalog_buttons.addWidget(add_button)
        catalog_buttons.addWidget(duplicate_button)
        catalog_buttons.addWidget(delete_button)
        left_panel.addLayout(catalog_buttons)

        center_panel = QVBoxLayout()
        columns.addLayout(center_panel, 3)
        center_panel.addWidget(QLabel("Настройки формата:"))
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.background_combo = QComboBox()
        self.background_combo.addItems([
            "Однотонный фон светлее лица",
            "Светлый фон",
            "Белый фон",
            "Серый фон",
            "Другой фон"
        ])
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0, 1000)
        self.width_spin.setSuffix(" мм")
        self.width_spin.setDecimals(1)
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(0, 1000)
        self.height_spin.setSuffix(" мм")
        self.height_spin.setDecimals(1)
        form.addRow("Название:", self.name_edit)
        form.addRow("Фон:", self.background_combo)
        form.addRow("Ширина:", self.width_spin)
        form.addRow("Высота:", self.height_spin)
        center_panel.addLayout(form)

        self.tabs = QTabWidget()
        center_panel.addWidget(self.tabs)

        basic_tab = QWidget()
        basic_layout = QFormLayout(basic_tab)
        self.markup_combo = QComboBox()
        self.markup_combo.addItems([
            "Разметка по двум точкам",
            "Разметка по трём точкам (ГОСТ)"
        ])
        self.top_margin = QDoubleSpinBox()
        self.bottom_margin = QDoubleSpinBox()
        self.face_size = QDoubleSpinBox()
        self.head_size = QDoubleSpinBox()
        for spin in (
            self.top_margin,
            self.bottom_margin,
            self.face_size,
            self.head_size
        ):
            spin.setRange(0, 1000)
            spin.setDecimals(1)
        basic_layout.addRow("Тип разметки:", self.markup_combo)
        basic_layout.addRow("Верхнее поле:", self.top_margin)
        basic_layout.addRow("Нижнее поле:", self.bottom_margin)
        basic_layout.addRow("Лицевая часть:", self.face_size)
        basic_layout.addRow("Размер головы:", self.head_size)
        self.tabs.addTab(basic_tab, "Основное")

        # Вкладка «Уголок».
        # Здесь хранятся размеры и тип уголка из исходной базы форматов.
        corner_tab = QWidget()
        corner_layout = QFormLayout(corner_tab)

        # Главный переключатель: использовать уголок для этого формата или нет.
        self.corner_enabled_check = QCheckBox("Уголок вкл./выкл.")
        corner_layout.addRow(self.corner_enabled_check)

        # Ширина уголка в единицах выбранного формата.
        self.corner_width_spin = QDoubleSpinBox()
        self.corner_width_spin.setRange(0, 1000)
        self.corner_width_spin.setDecimals(1)

        # Высота уголка в единицах выбранного формата.
        self.corner_height_spin = QDoubleSpinBox()
        self.corner_height_spin.setRange(0, 1000)
        self.corner_height_spin.setDecimals(1)

        # Тип уголка: левый или правый.
        # Внутри программы левый хранится как 4, правый как 5.
        self.corner_type_combo = QComboBox()
        self.corner_type_combo.addItem("Левый", 4)
        self.corner_type_combo.addItem("Правый", 5)

        corner_layout.addRow("Ширина уголка:", self.corner_width_spin)
        corner_layout.addRow("Высота уголка:", self.corner_height_spin)
        corner_layout.addRow("Тип уголка:", self.corner_type_combo)

        # Форма среза уголка: прямой или закруглённый.
        self.corner_shape_combo = QComboBox()
        self.corner_shape_combo.addItems([
            "Закруглённый срез",
            "Прямой срез"
        ])

        corner_layout.addRow(
            "Форма среза:",
            self.corner_shape_combo
        )

        # При выключенном уголке поля остаются видимыми, но становятся недоступными.
        self.corner_enabled_check.toggled.connect(
            self.corner_width_spin.setEnabled
        )
        self.corner_enabled_check.toggled.connect(
            self.corner_height_spin.setEnabled
        )
        self.corner_enabled_check.toggled.connect(
            self.corner_type_combo.setEnabled
        )

        self.corner_enabled_check.toggled.connect(
            self.corner_shape_combo.setEnabled
        )
        
        # Обновляем образец при изменении настроек уголка.
        self.corner_enabled_check.toggled.connect(
            self.update_sample_preview
        )
        self.corner_width_spin.valueChanged.connect(
            self.update_sample_preview
        )
        self.corner_height_spin.valueChanged.connect(
            self.update_sample_preview
        )
        self.corner_type_combo.currentIndexChanged.connect(
            self.update_sample_preview
        )
        self.corner_shape_combo.currentTextChanged.connect(
            self.update_sample_preview
        )

        self.tabs.addTab(corner_tab, "Уголок")

        extra_tab = QWidget()
        extra_layout = QVBoxLayout(extra_tab)
        extra_layout.addWidget(
            QLabel("Дополнительные параметры формата.")
        )
        self.tabs.addTab(extra_tab, "Дополнительно")


        right_panel = QVBoxLayout()
        columns.addLayout(right_panel, 2)

        right_panel.addWidget(QLabel("Образец фотографии:"))


        self.sample_photo = FormatPreview()

        preview_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "format_preview.png"
        )

        self.sample_photo_pixmap = QPixmap(preview_path)

        if not self.sample_photo_pixmap.isNull():
            self.sample_photo.set_photo(self.sample_photo_pixmap)


            self.sample_photo.set_landmarks(
                QPointF(300, 147),
                QPointF(300, 340),
                QPointF(274, 239),
                QPointF(326, 239)
            )


        self.sample_photo.set_format(
            self.width_spin.value(),
            self.height_spin.value(),
            self.top_margin.value(),
            self.head_size.value(),
            self.face_size.value(),
            self.markup_combo.currentText()
        )

        right_panel.addWidget(self.sample_photo, 1)

        self.black_white = QCheckBox("Черно-белое фото")
        self.black_white.toggled.connect(
            self.sample_photo.set_black_white
        )
        right_panel.addWidget(self.black_white)


        buttons = QDialogButtonBox()

        buttons.addButton(
            "Сохранить",
            QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.addButton(
            "Отмена",
            QDialogButtonBox.ButtonRole.RejectRole
        )

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)

        self.format_list.currentRowChanged.connect(self.load_selected_format)
        self.country_combo.currentTextChanged.connect(self.refresh_list)
        self.search_edit.textChanged.connect(self.refresh_list)
        self.markup_combo.currentTextChanged.connect(self.update_markup_fields)
        self.top_margin.valueChanged.connect(self.recalculate_head_size)
        self.bottom_margin.valueChanged.connect(self.recalculate_head_size)
        self.head_size.valueChanged.connect(self.recalculate_bottom_margin)
        self.height_spin.valueChanged.connect(self.recalculate_head_size)

        self.top_margin.valueChanged.connect(self.update_sample_preview)
        self.bottom_margin.valueChanged.connect(self.update_sample_preview)
        self.head_size.valueChanged.connect(self.update_sample_preview)
        self.face_size.valueChanged.connect(self.update_sample_preview)
        self.width_spin.valueChanged.connect(self.update_sample_preview)
        self.height_spin.valueChanged.connect(self.update_sample_preview)
        add_button.clicked.connect(self.add_format)
        duplicate_button.clicked.connect(self.duplicate_format)
        delete_button.clicked.connect(self.delete_format)
        self.refresh_list()


    def update_sample_preview(self, *_):
        width = self.width_spin.value()
        height = self.height_spin.value()

        if width <= 0 or height <= 0:
            return

        self.sample_photo.set_format(
            width,
            height,
            self.top_margin.value(),
            self.head_size.value(),
            self.face_size.value(),
            self.markup_combo.currentText()
        )

        # Передаём настройки уголка в образец фотографии.
        self.sample_photo.set_corner(
            self.corner_enabled_check.isChecked(),
            self.corner_width_spin.value(),
            self.corner_height_spin.value(),
            self.corner_type_combo.currentData(),
            self.corner_shape_combo.currentText()
        )


    def _set_combo_value(self, combo, value, default):
        # Получаем все варианты, доступные именно в этом списке.
        allowed_values = [
            combo.itemText(i)
            for i in range(combo.count())
        ]

        # Если сохранённое значение существует в списке — восстанавливаем его.
        if value not in allowed_values:
            value = default

        # Проверяем, что значение по умолчанию тоже существует.
        if default not in allowed_values:
            if allowed_values:
                value = allowed_values[0]
            else:
                return

        combo.setCurrentText(value)

    def update_markup_fields(self, _text=None):
        markup_type = self.markup_combo.currentText()

        if markup_type == "Разметка по двум точкам":
            self.top_margin.setEnabled(True)
            self.bottom_margin.setEnabled(True)
            self.face_size.setEnabled(False)
            self.head_size.setEnabled(True)

        elif markup_type == "Разметка по трём точкам (ГОСТ)":
            self.top_margin.setEnabled(True)
            self.bottom_margin.setEnabled(False)
            self.face_size.setEnabled(True)
            self.head_size.setEnabled(False)

        else:
            self.top_margin.setEnabled(True)
            self.bottom_margin.setEnabled(True)
            self.face_size.setEnabled(True)
            self.head_size.setEnabled(True)
            
    def recalculate_head_size(self, _value=None):
        if getattr(self, "_loading_format", False):
            return

        if self.current_index < 0:
            return

        if self.markup_combo.currentText() != "Разметка по двум точкам":
            return

        height = self.height_spin.value()
        height = self.height_spin.value()
        top = self.top_margin.value()
        bottom = self.bottom_margin.value()

        new_head = max(0, height - top - bottom)

        self.head_size.blockSignals(True)
        self.head_size.setValue(new_head)
        self.head_size.blockSignals(False)

    def recalculate_bottom_margin(self, _value=None):
        if getattr(self, "_loading_format", False):
            return

        if self.current_index < 0:
            return

        if self.markup_combo.currentText() != "Разметка по двум точкам":
            return

        height = self.height_spin.value()
        top = self.top_margin.value()
        head = self.head_size.value()

        new_bottom = max(0, height - top - head)

        self.bottom_margin.blockSignals(True)
        self.bottom_margin.setValue(new_bottom)
        self.bottom_margin.blockSignals(False)

    def select_format_by_index(self, format_index):
        for row in range(self.format_list.count()):
            list_item = self.format_list.item(row)

            if list_item.data(Qt.ItemDataRole.UserRole) == format_index:
                self.format_list.setCurrentRow(row)
                return

    def refresh_list(self, *_):
        self.save_current_format()
        self.format_list.blockSignals(True)
        self.format_list.clear()
        self.current_index = -1

        catalog_mode = self.country_combo.currentText()

        base_items = [
            (index, item)
            for index, item in enumerate(self.formats)
            if item.get("_is_base")
        ]

        custom_items = [
            (index, item)
            for index, item in enumerate(self.formats)
            if not item.get("_is_base")
        ]

        search_text = self.search_edit.text().strip().casefold()

        base_items = [
            (index, item)
            for index, item in base_items
            if search_text in str(item.get("name", "")).casefold()
        ]

        custom_items = [
            (index, item)
            for index, item in custom_items
            if search_text in str(item.get("name", "")).casefold()
        ]

        base_items.sort(
            key=lambda pair: pair[1].get("name", "").casefold()
        )
        custom_items.sort(
            key=lambda pair: pair[1].get("name", "").casefold()
        )

        groups = []

        if catalog_mode in ("Базовые форматы", "Все форматы"):
            groups.append(("БАЗОВЫЕ ФОРМАТЫ", base_items))

        if catalog_mode in ("Мои форматы", "Все форматы"):
            groups.append(("МОИ ФОРМАТЫ", custom_items))

        for title, items in groups:
            if not items:
                continue

            header = QListWidgetItem(title)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.format_list.addItem(header)

            for index, item in items:
                list_item = QListWidgetItem(
                    item.get("name", "Без названия")
                )
                list_item.setData(Qt.ItemDataRole.UserRole, index)

                source_base_name = item.get("source_base_name")
                if source_base_name:
                    list_item.setToolTip(
                        f"Создана на основе базового формата: {source_base_name}"
                    )

                if not item.get("_is_base"):
                    font = list_item.font()
                    font.setItalic(True)
                    list_item.setFont(font)

                self.format_list.addItem(list_item)

        if self.format_list.count() > 0:
            for row in range(self.format_list.count()):
                if self.format_list.item(row).data(
                    Qt.ItemDataRole.UserRole
                ) is not None:
                    self.format_list.setCurrentRow(row)
                    break

        self.format_list.blockSignals(False)

        if self.format_list.currentRow() >= 0:
            self.load_selected_format(self.format_list.currentRow())

    def load_selected_format(self, row):
        if row < 0:
            return

        list_item = self.format_list.item(row)
        if list_item is None:
            return

        index = list_item.data(Qt.ItemDataRole.UserRole)
        if index is None:
            return

        index = int(index)
        if index < 0 or index >= len(self.formats):
            return
        if (
            self.current_index >= 0
            and self.current_index < len(self.formats)
            and self.current_index != index
        ):
            self.save_current_format()

        self.current_index = index
        self._loading_format = True
        item = self.formats[index]

        units = item.get("units", "mm")
        unit_suffixes = {
            "mm": " мм",
            "in": " ″",
            "px": " px"
        }
        unit_suffix = unit_suffixes.get(units, " ед.")

        for spin in (
            self.width_spin,
            self.height_spin,
            self.top_margin,
            self.corner_width_spin,
            self.corner_height_spin,            
            self.bottom_margin,
            self.face_size,
            self.head_size
        ):
            spin.setSuffix(unit_suffix)

        self.name_edit.setText(item.get("name", ""))

        # Загружаем параметры уголка из исходных XML-данных формата.
        xml_data = item.get("xml_data", {})
        if not isinstance(xml_data, dict):
            xml_data = {}

        try:
            self.corner_width_spin.setValue(
                float(xml_data.get("CornerW", 0))
            )
        except (TypeError, ValueError):
            self.corner_width_spin.setValue(0)

        try:
            self.corner_height_spin.setValue(
                float(xml_data.get("CornerH", 0))
            )
        except (TypeError, ValueError):
            self.corner_height_spin.setValue(0)

        try:
            corner_type = int(xml_data.get("CornerType", 0))

            # Выбираем левый или правый уголок по значению из базы.
            index = self.corner_type_combo.findData(corner_type)
            self.corner_type_combo.setCurrentIndex(index)

        except (TypeError, ValueError):
            self.corner_type_combo.setCurrentIndex(-1)

        # Загружаем форму среза уголка.
        self._set_combo_value(
            self.corner_shape_combo,
            item.get("corner_shape"),
            "Закруглённый срез"
        )

        # Загружаем состояние переключателя уголка.
        # Для старых форматов включаем его только при известных типах 4 и 5.
        if "corner_enabled" in item:
            corner_enabled = bool(item["corner_enabled"])
        else:
            corner_enabled = self.corner_type_combo.currentData() in (4, 5)

        self.corner_enabled_check.setChecked(corner_enabled)

        # Обновляем доступность полей после загрузки формата.
        self.corner_width_spin.setEnabled(corner_enabled)
        self.corner_height_spin.setEnabled(corner_enabled)
        self.corner_type_combo.setEnabled(corner_enabled)

        width_val = item.get("width", 0)
        if width_val is None: width_val = 0
        try:
            self.width_spin.setValue(float(width_val))
        except (TypeError, ValueError):
            self.width_spin.setValue(0)

        height_val = item.get("height", 0)
        if height_val is None: height_val = 0
        try:
            self.height_spin.setValue(float(height_val))
        except (TypeError, ValueError):
            self.height_spin.setValue(0)
        self._set_combo_value(
            self.background_combo,
            item.get("background"),
            "Однотонный фон светлее лица"
        )
        self._set_combo_value(
            self.markup_combo,
            item.get("markup_type"),
            "Разметка по двум точкам"
        )

        self.update_markup_fields()

        val_top = item.get("top_margin")
        if val_top is None: val_top = item.get("border_top", 0)
        try: self.top_margin.setValue(float(val_top))
        except: self.top_margin.setValue(0)

        val_bot = item.get("bottom_margin")
        if val_bot is None: val_bot = item.get("border_bottom", 0)
        try: self.bottom_margin.setValue(float(val_bot))
        except: self.bottom_margin.setValue(0)

        val_face = item.get("face_size")
        if val_face is None: val_face = item.get("front_size", 0)
        try: self.face_size.setValue(float(val_face))
        except: self.face_size.setValue(0)

        val_head = item.get("head_size")

        if val_head is None:
            val_head = (
                self.height_spin.value()
                - self.top_margin.value()
                - self.bottom_margin.value()
            )

        try:
            self.head_size.setValue(float(val_head))
        except (TypeError, ValueError):
            self.head_size.setValue(0)
        self.update_sample_preview()
        self._loading_format = False
        self._loaded_format_values = self.get_editor_values()

    def get_editor_values(self):
        return {
            "name": self.name_edit.text().strip(),
            "width": self.width_spin.value(),
            "height": self.height_spin.value(),

            # Параметры уголка.
            "corner_width": self.corner_width_spin.value(),
            "corner_height": self.corner_height_spin.value(),
            "corner_type": self.corner_type_combo.currentData(),
            "corner_shape": self.corner_shape_combo.currentText(),

            "corner_enabled": self.corner_enabled_check.isChecked(),

            "background": self.background_combo.currentText(),
            "markup_type": self.markup_combo.currentText(),
            "top_margin": self.top_margin.value(),
            "bottom_margin": self.bottom_margin.value(),
            "face_size": self.face_size.value(),
            "head_size": self.head_size.value(),
        }

    def save_current_format(self):
        index = self.current_index

        if index < 0 or index >= len(self.formats):
            return

        item = self.formats[index]
        current_values = self.get_editor_values()

        if item.get("_is_base"):
            original_values = getattr(
                self,
                "_loaded_format_values",
                None
            )

            if current_values == original_values:
                return

        item.update(current_values)

        # Обновляем исходные XML-параметры уголка.
        # Остальные XML-данные формата сохраняем без изменений.
        xml_data = item.get("xml_data")

        if not isinstance(xml_data, dict):
            xml_data = {}

        xml_data["CornerW"] = str(current_values["corner_width"])
        xml_data["CornerH"] = str(current_values["corner_height"])
        xml_data["CornerType"] = str(current_values["corner_type"])

        item["xml_data"] = xml_data

        self._loaded_format_values = current_values.copy()

    def add_format(self):
        self.save_current_format()

        new_item = {
            "name": "Новый формат",
            "width": 35,    
            "height": 45,
            "background": "Белый фон",
            "markup_type": "Разметка по двум точкам",
            "top_margin": 5,
            "bottom_margin": 10,
            "head_size": 30,
            "note": "",
        }

        self.formats.append(new_item)
        new_index = len(self.formats) - 1

        self.country_combo.setCurrentText("Мои форматы")
        self.refresh_list()
        self.select_format_by_index(new_index)

        self.name_edit.selectAll()
        self.name_edit.setFocus()

    def duplicate_format(self):
        self.save_current_format()

        if self.current_index < 0:
            return

        new_item = copy.deepcopy(self.formats[self.current_index])

        new_item.pop("_is_base", None)
        new_item.pop("source_id", None)
        new_item.pop("_original_base", None)

        new_item["name"] = new_item.get("name", "Без названия") + " (копия)"

        self.formats.append(new_item)
        new_index = len(self.formats) - 1

        self.country_combo.setCurrentText("Мои форматы")
        self.refresh_list()
        self.select_format_by_index(new_index)

    def delete_format(self):
        index = self.current_index
        if index < 0:
            return
        self.save_current_format()
        answer = QMessageBox.question(
            self,
            "Удалить формат?",
            "Удалить выбранный формат?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.formats.pop(index)
        self.current_index = -1
        self.refresh_list()

    def get_formats(self):
        self.save_current_format()

        converted_copies = []

        base_data = {}

        try:
            with open(self.parent().base_formats_file, "r", encoding="utf-8") as file:
                data = json.load(file)

            for item in data.get("formats", []):
                source_id = str(item.get("source_id", ""))
                if source_id:
                    base_data[source_id] = item

        except (OSError, json.JSONDecodeError):
            return self.formats

        for index, item in enumerate(self.formats):
            if not item.get("_is_base"):
                continue

            source_id = str(item.get("source_id", ""))

            if not source_id or source_id not in base_data:
                continue

            original_item = base_data[source_id]

            current_data = copy.deepcopy(item)
            original_data = copy.deepcopy(original_item)

            for key in ("_is_base", "_original_base"):
                current_data.pop(key, None)
                original_data.pop(key, None)

            current_data.pop("source_id", None)
            original_data.pop("source_id", None)

            current_data.pop("_is_base", None)
            original_data.pop("_is_base", None)

            if current_data == original_data:
                continue

            new_item = copy.deepcopy(item)

            new_item.pop("_is_base", None)
            new_item.pop("source_id", None)
            new_item.pop("_original_base", None)

            new_item["source_base_id"] = source_id
            new_item["source_base_name"] = original_item.get("name", "")

            # Подбираем свободное название для копии формата.
            # Первая копия — «(копия)», следующие — «(копия2)», «(копия3)» и т. д.
            base_name = new_item.get("name", "Без названия")

            # Убираем старое обозначение копии, если оно уже есть.
            import re
            base_name = re.sub(r" \(копия\d*\)$", "", base_name)

            existing_names = {
                fmt.get("name", "").strip().casefold()
                for fmt in self.formats
                if fmt is not item
            }

            copy_number = 1

            while True:
                if copy_number == 1:
                    copy_name = f"{base_name} (копия)"
                else:
                    copy_name = f"{base_name} (копия{copy_number})"

                if copy_name.strip().casefold() not in existing_names:
                    break

                copy_number += 1

            new_item["name"] = copy_name

            restored_item = copy.deepcopy(original_item)
            restored_item["_is_base"] = True
            restored_item["source_id"] = source_id

            self.formats[index] = restored_item
            converted_copies.append(new_item)

        self.formats.extend(converted_copies)

        return self.formats

class ScreenColorPicker(QWidget):
    # Сигнал передаёт выбранный цвет обратно в PhotoStudio.
    colorSelected = Signal(QColor)

    def __init__(self):
        super().__init__()

        # Получаем список всех подключённых мониторов.
        self.screen_captures = []
        screens = QApplication.screens()

        # Определяем общую область всех мониторов.
        virtual_geometry = screens[0].geometry()

        for screen in screens:
            geometry = screen.geometry()
            virtual_geometry = virtual_geometry.united(geometry)

            # Снимаем изображение экрана до появления пипетки.
            pixmap = screen.grabWindow(0)

            if not pixmap.isNull():
                image = pixmap.toImage().convertToFormat(
                    QImage.Format.Format_RGB32
                )

                self.screen_captures.append({
                    "geometry": geometry,
                    "image": image
                })

        # Пипетка располагается поверх всех окон.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            True
        )

        self.setGeometry(virtual_geometry)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)

        # Текущая позиция курсора.
        self.cursor_global = QPoint(-1, -1)

        # Цвет под курсором.
        self.current_color = None

    def get_color_at(self, global_pos):
        # Ищем монитор, на котором находится курсор.
        for capture in self.screen_captures:
            geometry = capture["geometry"]

            if geometry.contains(global_pos):
                image = capture["image"]

                # Переводим координаты экрана в координаты изображения.
                scale_x = image.width() / geometry.width()
                scale_y = image.height() / geometry.height()

                x = int((global_pos.x() - geometry.x()) * scale_x)
                y = int((global_pos.y() - geometry.y()) * scale_y)

                x = max(0, min(x, image.width() - 1))
                y = max(0, min(y, image.height() - 1))

                return image.pixelColor(x, y)

        return None

    def mouseMoveEvent(self, event):
        # Запоминаем положение курсора на экране.
        self.cursor_global = event.globalPosition().toPoint()

        # Получаем цвет под курсором.
        self.current_color = self.get_color_at(
            self.cursor_global
        )

        self.update()

    def mousePressEvent(self, event):
        # Правая кнопка мыши отменяет выбор.
        if event.button() == Qt.MouseButton.RightButton:
            self.close()
            return

        # Левая кнопка выбирает цвет.
        if event.button() == Qt.MouseButton.LeftButton:
            color = self.get_color_at(
                event.globalPosition().toPoint()
            )

            if color is not None:
                self.colorSelected.emit(color)
                self.close()

    def keyPressEvent(self, event):
        # Esc отменяет работу пипетки.
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return

        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)

        # Слегка затемняем экран.
        painter.fillRect(
            self.rect(),
            QColor(0, 0, 0, 70)
        )

        if self.current_color is None:
            return

        # Находим монитор под курсором.
        capture = None

        for item in self.screen_captures:
            if item["geometry"].contains(self.cursor_global):
                capture = item
                break

        if capture is None:
            return

        geometry = capture["geometry"]
        image = capture["image"]

        # Координаты курсора на изображении монитора.
        scale_x = image.width() / geometry.width()
        scale_y = image.height() / geometry.height()

        x = int((self.cursor_global.x() - geometry.x()) * scale_x)
        y = int((self.cursor_global.y() - geometry.y()) * scale_y)

        # Вырезаем небольшую область вокруг курсора.
        radius = 12

        left = max(0, x - radius)
        top = max(0, y - radius)

        right = min(image.width(), x + radius + 1)
        bottom = min(image.height(), y + radius + 1)

        crop = image.copy(
            left,
            top,
            right - left,
            bottom - top
        )

        magnifier = QPixmap.fromImage(crop)

        # Размещаем увеличительное окно рядом с курсором.
        local_pos = self.cursor_global - self.geometry().topLeft()

        magnifier_x = local_pos.x() + 25
        magnifier_y = local_pos.y() + 25

        magnifier_size = 180

        if magnifier_x + magnifier_size > self.width():
            magnifier_x = local_pos.x() - magnifier_size - 25

        if magnifier_y + magnifier_size > self.height():
            magnifier_y = local_pos.y() - magnifier_size - 25

        magnifier_x = max(5, magnifier_x)
        magnifier_y = max(5, magnifier_y)

        # Рисуем увеличенную область.
        target = QRectF(
            magnifier_x,
            magnifier_y,
            magnifier_size,
            magnifier_size
        )

        painter.drawPixmap(
            target,
            magnifier,
            QRectF(magnifier.rect())
        )

        # Рамка увеличительного окна.
        painter.setPen(QPen(Qt.GlobalColor.white, 3))
        painter.drawRect(target)

        # Показываем цвет и его код.
        color_text = (
            f"{self.current_color.name()}  "
            f"RGB: {self.current_color.red()}, "
            f"{self.current_color.green()}, "
            f"{self.current_color.blue()}"
        )

        painter.setPen(Qt.GlobalColor.white)

        painter.drawText(
            QRectF(
                magnifier_x,
                magnifier_y + magnifier_size + 8,
                330,
                30
            ),
            Qt.AlignmentFlag.AlignLeft,
            color_text
        )

        painter.drawText(
            QRectF(
                magnifier_x,
                magnifier_y + magnifier_size + 32,
                330,
                30
            ),
            Qt.AlignmentFlag.AlignLeft,
            "ЛКМ — выбрать цвет, Esc — отмена"
        )

    def start_picker(self):
        # Показываем пипетку и передаём ей фокус.
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()


class ExportOptionsDialog(QDialog):
    """PPI and explicit re-selection of the locked physical format."""

    def __init__(self, format_info, previous_size=None, parent=None,
                 selection_changed=False):
        super().__init__(parent)
        self.setWindowTitle("Параметры экспорта")
        self.format_info = format_info
        self.previous_size = previous_size
        self._size = None
        try:
            # При первом открытии формат выбирается с исходным PPI 300.
            # Дальнейшее изменение одного PPI сохраняет этот растр.
            self.baseline_size = previous_size or calculate_export_size(
                format_info.get("width"), format_info.get("height"),
                format_info.get("units", "mm"), 300,
            )
        except (ValueError, TypeError, OverflowError):
            # Если 300 PPI не даёт допустимого растра, сначала нужен
            # явный расчёт формата при другом разрешении.
            self.baseline_size = None

        layout = QFormLayout(self)
        description = QLabel(
            f"{format_info.get('name', 'Выбранный формат')}: "
            f"{format_info.get('width')} × {format_info.get('height')} "
            f"{format_info.get('units', 'mm')}"
        )
        description.setTextFormat(Qt.TextFormat.PlainText)
        description.setWordWrap(True)
        layout.addRow("Зафиксированный формат:", description)
        if selection_changed:
            notice = QLabel(
                "Формат в списке изменён после фиксации. "
                "Экспортируется формат зафиксированной композиции."
            )
            notice.setWordWrap(True)
            layout.addRow(notice)

        self.ppi_spin = QSpinBox(self)
        self.ppi_spin.setRange(MIN_DPI, MAX_DPI)
        self.ppi_spin.setValue(previous_size.dpi if previous_size else 300)
        layout.addRow("Разрешение, PPI:", self.ppi_spin)

        self.recalculate_check = QCheckBox(
            "Пересчитать пиксели по зафиксированному формату", self
        )
        self.recalculate_check.setChecked(self.baseline_size is None)
        self.recalculate_check.setEnabled(self.baseline_size is not None)
        self.recalculate_check.setToolTip(
            "Без повторного выбора формата изменение PPI не меняет размер в пикселях."
        )
        layout.addRow(self.recalculate_check)

        self.preview_label = QLabel(self)
        self.preview_label.setWordWrap(True)
        layout.addRow("Результат:", self.preview_label)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Далее")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)
        self.ppi_spin.valueChanged.connect(self._update_preview)
        self.recalculate_check.toggled.connect(self._update_preview)
        self._update_preview()

    def _update_preview(self, *_):
        try:
            dpi = self.ppi_spin.value()
            if self.baseline_size is not None and not self.recalculate_check.isChecked():
                size = calculate_export_size(
                    self.baseline_size.width_px, self.baseline_size.height_px, "px", dpi
                )
            else:
                size = calculate_export_size(
                    self.format_info.get("width"), self.format_info.get("height"),
                    self.format_info.get("units", "mm"), dpi,
                )
            width_mm, height_mm = estimate_print_size_mm(size.width_px, size.height_px, dpi)
        except (ValueError, TypeError, OverflowError) as error:
            self._size = None
            self.preview_label.setText(f"Невозможно рассчитать размер: {error}")
        else:
            self._size = size
            self.preview_label.setText(
                f"{size.width_px} × {size.height_px} px\n"
                f"При {dpi} PPI ≈ {width_mm:.6f} × {height_mm:.6f} мм"
            )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self._size is not None)

    def selected_size(self):
        return self._size


def _export_target_path(path, selected_filter):
    """Choose the encoder by explicit extension, or append the selected one."""
    extension = os.path.splitext(path)[1].lower()
    if extension == ".png":
        return path, "png"
    if extension in (".jpg", ".jpeg"):
        return path, "jpeg"
    if extension:
        raise ValueError("Выберите файл с расширением .png, .jpg или .jpeg.")
    if selected_filter == EXPORT_JPEG_FILTER:
        return path + ".jpg", "jpeg"
    return path + ".png", "png"


def _same_export_path(first, second):
    """Protect the source even if the chosen path is a link or alias."""
    if not first or not second:
        return False
    try:
        if os.path.samefile(first, second):
            return True
    except OSError:
        pass
    return os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(second))


def _write_export_image(path, image, image_format):
    """Write the prepared image atomically; leave an existing file intact on failure."""
    target = QSaveFile(path)
    target.setDirectWriteFallback(False)
    if not target.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(target.errorString())
    try:
        writer = QImageWriter(target, b"JPEG" if image_format == "jpeg" else b"PNG")
        if image_format == "jpeg":
            writer.setQuality(95)
        if not writer.write(image):
            raise OSError(writer.errorString())
        if not target.commit():
            raise OSError(target.errorString())
    except Exception:
        target.cancelWriting()
        raise


class PhotoStudio(QMainWindow):
    def __init__(self):
        super().__init__()
        # resizeEvent может прийти до открытия первой фотографии.
        self.current_pixmap = QPixmap()
        self.settings = QSettings("PhotoStudio", "PhotoStudio")
        self.setWindowTitle("Фото на документы — PhotoStudio")

        geometry = self.settings.value("window_geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1250, 780)

        # Формат папки может открыть фото ещё до первого выбора формата.
        self.current_markup_type = "Разметка по двум точкам"
        self.photo_document = None
        self._export_size = None
        self._export_cache = None
        self.composite_renderer = CompositeRenderer()
        # background_mask_edit is M_edit: the source mask modified by brushes.
        # background_mask remains a compatibility alias used by older handlers.
        self.background_mask_edit = None
        self.background_mask = None
        self.background_feathered_mask = None
        self.background_mask_feather = MaskFeather()
        self.background_protected_mask = None

        # Сохранённый масштаб и положение оригинала в режиме разметки.
        self.markup_state_before_lock = None
        self.markup_saved_zoom = 1.0
        self.markup_saved_pan_x = 0.0
        self.markup_saved_pan_y = 0.0
        
        self.current_folder = None

        # -------------------------------------------------
        # НАСТРОЙКИ БЫСТРОЙ ЗАГРУЗКИ МИНИАТЮР
        # -------------------------------------------------

        # Пул потоков для параллельной загрузки миниатюр.
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(WORKERS)

        # Сигнал для передачи готовых миниатюр в главное окно.
        self.thumbnail_signals = ThumbnailSignals()
        self.thumbnail_signals.finished.connect(
            self.on_thumbnail_loaded
        )

        # Список фотографий текущей папки.
        self.photo_files = []

        # Связь полного пути к фотографии с элементом сетки.
        self.items_by_path = {}

        # Пути фотографий, миниатюры которых уже загружаются.
        self.loading_paths = set()

        # Последний обработанный видимый диапазон сетки.
        self.last_visible_range = (-1, -1)


        self.formats = []
        # Последние 8 использованных форматов и избранное
        self.recent_format_ids = self.settings.value(
            "recent_format_ids", [], type=list
        )
        self.favorite_format_ids = self.settings.value(
            "favorite_format_ids", [], type=list
        )

        app_data_dir = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            "PhotoStudio"
        )
        os.makedirs(app_data_dir, exist_ok=True)

        self.formats_file = os.path.join(
            app_data_dir,
            "formats_custom.json"
        )

        old_formats_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "formats_custom.json"
        )
        if (
            not os.path.exists(self.formats_file)
            and os.path.isfile(old_formats_file)
        ):
            with open(old_formats_file, "rb") as source_file:
                initial_data = source_file.read()
            with open(self.formats_file, "wb") as target_file:
                target_file.write(initial_data)

        self.base_formats_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "formats_ru.json"
        )
        self._deleted_base_ids = []
        self.load_formats()
        self.create_menu()
        self.create_interface()
        
        saved_mode = self.settings.value("current_mode", "Форматы фотографий")
        if saved_mode in ("Форматы фотографий", "Разметка"):
            self.mode_combo.setCurrentText(saved_mode)

        dock_state = self.settings.value("dock_state")
        if dock_state:
            self.restoreState(dock_state)        

        last_folder = self.settings.value("last_folder", "")
        if last_folder and os.path.isdir(last_folder):
            index = self.folder_model.index(last_folder)
            if index.isValid():
                self.folder_tree.setCurrentIndex(index)
                self.folder_tree.scrollTo(index)
                self.on_folder_selected(index)
        self.refresh_format_list()

    # -----------------------------------------------------
    # ПРИЁМ ФОТОГРАФИЙ ИЗ ПРОВОДНИКА WINDOWS
    # -----------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if not hasattr(self, "files_list"):
            event.ignore()
            return

        position = self.files_list.viewport().mapFrom(
            self,
            event.position().toPoint()
        )

        if (
            self.files_list.viewport().rect().contains(position)
            and event.mimeData().hasUrls()
        ):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        position = self.files_list.viewport().mapFrom(
            self,
            event.position().toPoint()
        )

        if not self.files_list.viewport().rect().contains(position):
            event.ignore()
            return

        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]

        if paths:
            self.copy_dropped_photos(paths)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def closeEvent(self, event):
        self.settings.setValue("window_geometry", self.saveGeometry())
        self.settings.setValue("dock_state", self.saveState())
        self.settings.setValue("current_mode", self.mode_combo.currentText())
        self.settings.sync()
        event.accept()        

    def auto_detect_face(self):
        # Двойной щелчок не должен повторно включать разметку
        # поверх уже зафиксированной рабочей копии.
        if self.photo_view.composition_locked:
            return

        if self.current_pixmap.isNull():
            QMessageBox.warning(
                self,
                "Нет фотографии",
                "Сначала открой фотографию."
            )
            return
        

        # Поиск выполняем только внутри текущей рамки кадрирования.
        view = self.photo_view

        image_bounds = QRectF(
            0,
            0,
            view.pixmap.width(),
            view.pixmap.height()
        )

        search_rect = view.crop_rect.intersected(image_bounds)
        search_rect_int = search_rect.toAlignedRect()

        if search_rect_int.isEmpty():
            QMessageBox.warning(
                self,
                "Некорректная рамка",
                "Рамка кадрирования не попадает в фотографию."
            )
            return

        # Вырезаем только область внутри рамки.
        search_pixmap = view.pixmap.copy(search_rect_int)

        try:
            result = detect_face_and_eyes(search_pixmap)
        except Exception as error:
            QMessageBox.critical(
                self,
                "Ошибка распознавания",
                f"Не удалось выполнить распознавание лица:\n{error}"
            )
            return


        if result is None:
            QMessageBox.information(
                self,
                "Распознавание",
                "Не удалось найти лицо и оба глаза. "
                "Попробуй фотографию, где лицо хорошо видно."
            )
            return

        face_rect, left_eye, right_eye, head_top, chin = result

        # Переводим координаты из вырезанной области
        # обратно в координаты всей фотографии.
        offset_x = search_rect_int.x()
        offset_y = search_rect_int.y()

        face_rect.translate(offset_x, offset_y)

        left_eye += QPointF(offset_x, offset_y)
        right_eye += QPointF(offset_x, offset_y)

        if head_top is not None:
            head_top += QPointF(offset_x, offset_y)

        if chin is not None:
            chin += QPointF(offset_x, offset_y)

        self.photo_view.face_rect = face_rect
        self.photo_view.left_eye = left_eye
        self.photo_view.right_eye = right_eye
        self.photo_view.head_top = head_top
        self.photo_view.chin = chin

        self.mode_combo.setCurrentText("Разметка")
        self.photo_view.set_markup_enabled(True)
        self.photo_view.update()

    def load_formats(self):
        print("ФАЙЛ ФОРМАТОВ:", self.formats_file)

        self.formats = []
        self._deleted_base_ids = []
        custom_formats = []

        if os.path.exists(self.formats_file):
            try:
                with open(self.formats_file, "r", encoding="utf-8") as file:
                    saved_data = json.load(file)

                if isinstance(saved_data, list):
                    custom_formats = saved_data

                elif isinstance(saved_data, dict):
                    custom_formats = saved_data.get("custom_formats", [])
                    self._deleted_base_ids = saved_data.get(
                        "deleted_base_ids", []
                    )

            except (OSError, json.JSONDecodeError):
                QMessageBox.warning(
                    self,
                    "Предупреждение",
                    "Не удалось прочитать formats_custom.json."
                )

        if not os.path.exists(self.base_formats_file):
            QMessageBox.critical(
                self,
                "Нет базы форматов",
                "Не найден файл formats_ru.json.\n"
                "Положи его рядом с main.py."
            )
            return

        try:
            with open(self.base_formats_file, "r", encoding="utf-8") as file:
                base_data = json.load(file)

            base_formats = base_data.get("formats", [])

            for source_item in base_formats:
                item = dict(source_item)

                source_id = str(item.get("source_id", ""))

                if source_id in self._deleted_base_ids:
                    continue

                item["_is_base"] = True
                item["source_id"] = source_id

                self.formats.append(item)

        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            QMessageBox.critical(
                self,
                "Ошибка базы форматов",
                f"Не удалось загрузить formats_ru.json:\n{error}"
            )
            return

        # Проверяем, пришлось ли создавать новые ID форматов.
        favorite_ids_created = False

        for item in custom_formats:
            if not item.get("custom", True):
                continue

            # Если у формата нет постоянного ID — создаём его.
            if not item.get("_favorite_id"):
                item["_favorite_id"] = str(uuid.uuid4())
                favorite_ids_created = True

            self.formats.append(item)

        # Сразу сохраняем новые ID, чтобы при следующем запуске
        # они не создавались заново и избранное не теряло связь.
        if favorite_ids_created:
            self.save_formats()

    def save_formats(self):
        custom_formats = []

        for item in self.formats:
            if not item.get("_is_base"):
                custom_formats.append(item)

        saved_data = {
            "custom_formats": custom_formats,
            "deleted_base_ids": self._deleted_base_ids
        }

        try:
            with open(self.formats_file, "w", encoding="utf-8") as file:
                json.dump(
                    saved_data,
                    file,
                    ensure_ascii=False,
                    indent=4
                )

        except OSError as error:
            QMessageBox.critical(
                self,
                "Ошибка сохранения",
                f"Не удалось сохранить форматы:\n{error}"
            )

    def create_menu(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("Файл")
        open_photo_action = QAction("Открыть фотографию", self)
        open_photo_action.triggered.connect(self.open_photo)
        file_menu.addAction(open_photo_action)

        open_folder_action = QAction("Открыть папку с фотографиями", self)
        open_folder_action.triggered.connect(self.open_folder)
        file_menu.addAction(open_folder_action)

        file_menu.addSeparator()
        export_action = QAction("Экспорт композиции…", self)
        export_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        export_action.triggered.connect(self.export_composition)
        file_menu.addAction(export_action)

        file_menu.addSeparator()
        exit_action = QAction("Выход", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        mode_menu = menu_bar.addMenu("Режим")
        formats_action = QAction("Форматы фотографий", self)
        formats_action.triggered.connect(lambda: self.change_mode("formats"))
        mode_menu.addAction(formats_action)

        markup_action = QAction("Разметка", self)
        markup_action.triggered.connect(lambda: self.change_mode("markup"))
        mode_menu.addAction(markup_action)

    def export_composition(self):
        document = self.photo_document
        if document is None or not self.photo_view.composition_locked:
            QMessageBox.warning(
                self, "Экспорт композиции",
                "Сначала откройте фотографию и зафиксируйте композицию."
            )
            return
        if self._background_border_slider_dragging:
            QMessageBox.warning(
                self, "Экспорт композиции",
                "Отпустите ползунок границы фона перед экспортом."
            )
            return

        format_info = document.metadata.get("export_format")
        if not isinstance(format_info, dict):
            QMessageBox.warning(
                self, "Экспорт композиции",
                "У зафиксированной композиции нет формата. Вернитесь в разметку, "
                "выберите формат и зафиксируйте композицию заново."
            )
            return

        options = ExportOptionsDialog(
            format_info, self._export_size, self,
            selection_changed=format_info != self.format_combo.currentData(),
        )
        if options.exec() != QDialog.DialogCode.Accepted:
            return
        size = options.selected_size()
        if size is None:
            QMessageBox.warning(self, "Экспорт композиции", "Некорректный размер экспорта.")
            return
        if self.photo_document is not document or not self.photo_view.composition_locked:
            QMessageBox.warning(
                self, "Экспорт композиции", "Композиция изменилась. Начните экспорт заново."
            )
            return

        source_path = getattr(self, "current_photo_path", None) or document.source_path
        if source_path:
            name = os.path.splitext(os.path.basename(source_path))[0] + "_готово"
            suggested_path = os.path.join(os.path.dirname(source_path), name)
        else:
            suggested_path = ""
        default_filter = (
            EXPORT_JPEG_FILTER
            if self._export_cache is not None and self._export_cache["format"] == "jpeg"
            else EXPORT_PNG_FILTER
        )
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Экспорт композиции", suggested_path,
            f"{EXPORT_PNG_FILTER};;{EXPORT_JPEG_FILTER}",
            default_filter, QFileDialog.Option.DontConfirmOverwrite,
        )
        if not path:
            return
        try:
            path, image_format = _export_target_path(path, selected_filter or default_filter)
        except ValueError as error:
            QMessageBox.warning(self, "Экспорт композиции", str(error))
            return

        def is_source_path():
            return any(
                _same_export_path(path, source)
                for source in (document.source_path, getattr(self, "current_photo_path", None))
            )

        if is_source_path():
            QMessageBox.warning(
                self, "Экспорт композиции",
                "Нельзя перезаписать исходную фотографию. Выберите другое имя."
            )
            return
        if os.path.lexists(path):
            answer = QMessageBox.question(
                self, "Замена файла", f"Файл уже существует:\n{path}\n\nЗаменить его?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        # Модальные диалоги обрабатывают события: перед снимком проверяем
        # документ и применяем последнее ожидающее обновление ползунка.
        if self.photo_document is not document or not self.photo_view.composition_locked:
            QMessageBox.warning(
                self, "Экспорт композиции", "Композиция изменилась. Начните экспорт заново."
            )
            return
        if self._background_border_slider_dragging:
            QMessageBox.warning(
                self, "Экспорт композиции", "Отпустите ползунок границы фона перед экспортом."
            )
            return
        if is_source_path():
            QMessageBox.warning(
                self, "Экспорт композиции",
                "Нельзя перезаписать исходную фотографию. Выберите другое имя."
            )
            return

        try:
            if self.background_border_preview_timer.isActive():
                self.background_border_preview_timer.stop()
                try:
                    self._refresh_background_border_preview()
                except Exception:
                    # Не теряем отложенное изменение при ошибке подготовки.
                    self.background_border_preview_timer.start()
                    raise

            # Проверяем размер холста до выделения памяти под композит.
            canvas = document.canvas_size
            calculate_export_size(canvas.width(), canvas.height(), "px", size.dpi)
            # Сверяем хэш пикселей композита, а не предпросмотр виджета:
            # изменения слоёв, масок или видимости обновляют кэш экспорта.
            composite = self.composite_renderer.render(document)
            if composite.isNull() or composite.size() != document.canvas_size:
                raise RuntimeError("Не удалось создать композит документа")
            signature = hashlib.sha256(composite.constBits()).digest()
            del composite
            cache = self._export_cache
            if (
                cache is not None
                and cache["document"] is document
                and cache["format"] == image_format
                and cache["image"].size() == QSize(size.width_px, size.height_px)
                and cache["signature"] == signature
            ):
                # Изменение только PPI готового растра не вызывает ресэмплинг.
                image = set_image_dpi(cache["image"], size.dpi)
            else:
                image = prepare_export_image(
                    document, size.width_px, size.height_px, "px", size.dpi,
                    image_format=image_format,
                )
            _write_export_image(path, image, image_format)
        except Exception as error:
            QMessageBox.critical(
                self, "Ошибка экспорта",
                f"Не удалось сохранить композицию:\n{str(error) or type(error).__name__}",
            )
            return

        self._export_size = size
        self._export_cache = {
            "document": document, "format": image_format,
            "signature": signature, "image": image,
        }
        self.statusBar().showMessage(
            f"Сохранено: {path} ({size.width_px} × {size.height_px} px, {size.dpi} PPI)"
        )

    def open_format_settings(self):
        dialog = FormatSettingsDialog(self.formats, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        old_base_ids = {
            item.get("source_id")
            for item in self.formats
            if item.get("_is_base")
        }
        new_formats = dialog.get_formats()
        new_base_ids = {
            item.get("source_id")
            for item in new_formats
            if item.get("_is_base")
        }
        for source_id in old_base_ids - new_base_ids:
            if source_id and source_id not in self._deleted_base_ids:
                self._deleted_base_ids.append(source_id)

        self.formats = new_formats
        self.save_formats()
        self.refresh_format_list()

    def create_interface(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        toolbar = QToolBar("Основная панель")
        toolbar.setObjectName("main_toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        open_action = QAction("Открыть фото", self)
        open_action.triggered.connect(self.open_photo)
        toolbar.addAction(open_action)

        folder_action = QAction("Открыть папку", self)
        folder_action.triggered.connect(self.open_folder)
        toolbar.addAction(folder_action)
        toolbar.addSeparator()

        self.mode_combo = QComboBox()
        self.mode_combo.addItems([
            "Форматы фотографий",
            "Разметка",
            "Обработка"
        ])
        self.mode_combo.currentTextChanged.connect(
            self.on_mode_combo_changed
        )
        toolbar.addWidget(self.mode_combo)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        work_splitter = QSplitter(Qt.Orientation.Vertical)
        work_splitter.setChildrenCollapsible(False)
        work_splitter.setHandleWidth(8)

        self.files_panel = QWidget()
        files_layout = QVBoxLayout(self.files_panel)
        files_title = QLabel("Проводник")
        files_layout.addWidget(files_title)

        self.left_splitter = QSplitter(Qt.Orientation.Vertical)

        self.left_splitter.setAcceptDrops(True)

        self.left_splitter.setChildrenCollapsible(False)
        self.left_splitter.setHandleWidth(7)

        # Модель папок с проверкой наличия вложенных папок.
        self.folder_model = FolderOnlyFileSystemModel(self)
        self.folder_model.setRootPath("")
        self.folder_model.setFilter(
            QDir.Filter.AllDirs |
            QDir.Filter.NoDotAndDotDot |
            QDir.Filter.Drives
        )
        # Дерево папок с поддержкой перетаскивания фотографий.
        self.folder_tree = PhotoFolderTree(
            self.move_photos_to_folder
        )
        self.folder_tree.setModel(self.folder_model)

        # Разрешаем принимать фотографии, перетащенные из сетки.
        self.folder_tree.setAcceptDrops(True)
        self.folder_tree.setDropIndicatorShown(True)
        self.folder_tree.setDragDropMode(
            QAbstractItemView.DragDropMode.DropOnly
        )

        self.folder_tree.setRootIndex(self.folder_model.index(""))
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.hideColumn(1)
        self.folder_tree.hideColumn(2)
        self.folder_tree.hideColumn(3)
        self.folder_tree.clicked.connect(self.on_folder_selected)
        self.left_splitter.addWidget(self.folder_tree)

        # Сетка фотографий с поддержкой перетаскивания.
        self.files_list = PhotoGrid()
        
        # Обработчик фотографий, перетащенных из Проводника Windows.
        self.files_list.drop_callback = self.copy_dropped_photos
        
        # Разрешаем выделять несколько фотографий.
        self.files_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.files_list.setViewMode(QListView.ViewMode.IconMode)
        self.files_list.setIconSize(QSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE))
        self.files_list.setGridSize(QSize(ITEM_WIDTH, ITEM_HEIGHT))
        self.files_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.files_list.setMovement(QListView.Movement.Static)
        self.files_list.setWordWrap(True)
        self.files_list.setWrapping(True)
        self.files_list.setSpacing(8)
        self.files_list.setUniformItemSizes(True)

        # Один щелчок только выбирает фотографию.
        self.files_list.currentItemChanged.connect(
            self.on_current_item_changed
        )

        # При прокрутке проверяем, какие миниатюры стали видимыми.
        self.files_list.verticalScrollBar().valueChanged.connect(
            self.schedule_thumbnail_loading
        )

        # Двойной щелчок открывает фотографию в редакторе.
        self.files_list.itemDoubleClicked.connect(
            self.open_selected_photo
        )

        # Вызов контекстного меню правой кнопкой мыши.
        self.files_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )

        self.files_list.customContextMenuRequested.connect(
            self.show_photo_context_menu
        )

        self.left_splitter.addWidget(self.files_list)

        self.file_preview = QLabel("Предпросмотр")
        self.file_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_preview.setMinimumHeight(80)
        self.file_preview.setStyleSheet(
            "background-color: #303030; color: white;"
        )
        self.left_splitter.addWidget(self.file_preview)

        self.left_splitter.setStretchFactor(0, 3)
        self.left_splitter.setStretchFactor(1, 3)
        self.left_splitter.setStretchFactor(2, 2)
        self.left_splitter.setSizes([250, 250, 160])
        files_layout.addWidget(self.left_splitter)
        self.files_dock = QDockWidget("Проводник", self)
        self.files_dock.setObjectName("files_dock")
        self.files_dock.setAllowedAreas(
            Qt.DockWidgetArea.AllDockWidgetAreas
        )
        self.files_dock.setWidget(self.files_panel)
        self.addDockWidget(
            Qt.DockWidgetArea.LeftDockWidgetArea,
            self.files_dock
        )

        self.photo_view = MarkupView()
        self.setCentralWidget(self.photo_view)
        self.photo_view.double_click_callback = self.auto_detect_face

        # Подключаем ручную коррекцию маски удаления фона.
        self.photo_view.background_brush_callback = (
            self.apply_background_brush
        )

        self.detect_button = QPushButton("Найти лицо и глаза")
        self.detect_button.clicked.connect(self.auto_detect_face)

        self.format_panel = QWidget()
        format_layout = QVBoxLayout(self.format_panel)
        format_title = QLabel("Формат фотографии")
        format_layout.addWidget(format_title)

        self.format_settings_button = QPushButton("Настройка")
        self.format_settings_button.clicked.connect(self.open_format_settings)
        format_layout.addWidget(self.format_settings_button)        

        self.format_search = QLineEdit()
        self.format_search.setPlaceholderText("Поиск формата...")
        self.format_search.textChanged.connect(self.refresh_format_list)
        format_layout.addWidget(self.format_search)

        self.format_recent_list = QListWidget()
        self.format_recent_list.setFixedHeight(76)
        self.format_recent_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.format_recent_list.itemClicked.connect(
            self.on_format_preview_clicked
        )
        format_layout.addWidget(self.format_recent_list)

        format_row = QHBoxLayout()

        self.format_combo = QComboBox()
        self.format_combo.setMaxVisibleItems(8)
        self.format_combo.currentIndexChanged.connect(self.on_format_selected)
        format_row.addWidget(self.format_combo, 1)

        self.favorite_format_button = QPushButton("☆")
        self.favorite_format_button.setToolTip(
            "Добавить формат в избранное"
        )
        self.favorite_format_button.setFixedWidth(38)
        self.favorite_format_button.clicked.connect(
            self.toggle_favorite_format
        )
        format_row.addWidget(self.favorite_format_button)

        format_layout.addLayout(format_row)

        self.format_info = QLabel()
        self.format_info.setWordWrap(True)
        self.format_info.setMinimumHeight(65)
        format_layout.addWidget(self.format_info)

        edit_buttons = QHBoxLayout()
        self.edit_format_button = QPushButton("Изменить")
        self.edit_format_button.clicked.connect(self.edit_format)
        edit_buttons.addWidget(self.edit_format_button)

        self.add_format_button = QPushButton("Добавить")
        self.add_format_button.clicked.connect(self.add_format)
        edit_buttons.addWidget(self.add_format_button)
        format_layout.addLayout(edit_buttons)

        self.delete_format_button = QPushButton("Удалить формат")
        self.delete_format_button.clicked.connect(self.delete_format)
        format_layout.addWidget(self.delete_format_button)

        format_layout.addStretch()
        # Правая боковая панель с настройками выбранного формата.
        self.format_dock = QDockWidget("Формат", self)
        self.format_dock.setObjectName("format_dock")
        self.format_dock.setAllowedAreas(
            Qt.DockWidgetArea.AllDockWidgetAreas
        )
        self.format_dock.setWidget(self.format_panel)
        self.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            self.format_dock
        )


        self.markup_group = QGroupBox("Разметка фотографии")
        markup_layout = QVBoxLayout(self.markup_group)
        markup_layout.addWidget(self.detect_button)

        # Первый ряд: рамка и две точки горизонтали.
        markup_row1 = QHBoxLayout()


        self.left_eye_button = QPushButton(
            "Раз глаз"
        )
        self.left_eye_button.clicked.connect(
            lambda: self.set_markup_tool("left_eye")
        )
        markup_row1.addWidget(self.left_eye_button)

        self.right_eye_button = QPushButton(
            "Два глаз"
        )
        self.right_eye_button.clicked.connect(
            lambda: self.set_markup_tool("right_eye")
        )
        markup_row1.addWidget(self.right_eye_button)

        markup_layout.addLayout(markup_row1)

        # Второй ряд: макушка, подбородок и горизонталь.
        markup_row2 = QHBoxLayout()

        self.head_top_button = QPushButton("Макушка")
        self.head_top_button.clicked.connect(
            lambda: self.set_markup_tool("head_top")
        )
        markup_row2.addWidget(self.head_top_button)

        self.chin_button = QPushButton("Подбородок")
        self.chin_button.clicked.connect(
            lambda: self.set_markup_tool("chin")
        )
        markup_row2.addWidget(self.chin_button)

        self.align_eyes_button = QPushButton("По горизонту")
        self.align_eyes_button.clicked.connect(
            self.align_photo_by_eyes
        )
        markup_row2.addWidget(self.align_eyes_button)

        markup_layout.addLayout(markup_row2)

        # Третий ряд: вертикаль и сброс.
        markup_row3 = QHBoxLayout()

        self.align_vertical_button = QPushButton(
            "По вертикальной оси"
        )
        self.align_vertical_button.clicked.connect(
            self.align_photo_by_vertical_axis
        )
        markup_row3.addWidget(self.align_vertical_button)

        self.reset_marks_button = QPushButton("Разметить снова")
        self.reset_marks_button.clicked.connect(self.reset_markup)
        markup_row3.addWidget(self.reset_marks_button)

        markup_layout.addLayout(markup_row3)

        # Подгонка верхнего и нижнего полей.
        margin_row = QHBoxLayout()

        self.top_margin_label = QLabel("Верхнее поле, мм:")
        margin_row.addWidget(self.top_margin_label)

        self.top_margin_input = QDoubleSpinBox()
        self.top_margin_input.setRange(0.0, 999999.9)
        self.top_margin_input.setDecimals(1)
        self.top_margin_input.setSingleStep(0.1)
        self.top_margin_input.setValue(3.0)
        margin_row.addWidget(self.top_margin_input)

        self.bottom_margin_label = QLabel("Нижнее поле, мм:")
        margin_row.addWidget(self.bottom_margin_label)

        self.bottom_margin_input = QDoubleSpinBox()
        self.bottom_margin_input.setRange(0.0, 999999.9)
        self.bottom_margin_input.setDecimals(1)
        self.bottom_margin_input.setSingleStep(0.1)
        self.bottom_margin_input.setValue(3.0)
        margin_row.addWidget(self.bottom_margin_input)

        self.apply_margins_button = QPushButton("Применить")
        self.apply_margins_button.clicked.connect(
            self.apply_face_margins
        )
        margin_row.addWidget(self.apply_margins_button)

        markup_layout.addLayout(margin_row)

        # Размер головы и лицевой части.
        size_row = QHBoxLayout()

        self.head_size_label = QLabel("Размер головы, мм:")
        size_row.addWidget(self.head_size_label)

        self.head_size_input = QDoubleSpinBox()
        self.head_size_input.setRange(0.0, 999999.9)
        self.head_size_input.setDecimals(1)
        self.head_size_input.setSingleStep(0.1)
        self.head_size_input.setValue(0.0)
        size_row.addWidget(self.head_size_input)

        self.face_size_label = QLabel("Лицевая часть, мм:")
        size_row.addWidget(self.face_size_label)

        self.face_size_input = QDoubleSpinBox()
        self.face_size_input.setRange(0.0, 999999.9)
        self.face_size_input.setDecimals(1)
        self.face_size_input.setSingleStep(0.1)
        self.face_size_input.setValue(0.0)
        self.head_size_input.valueChanged.connect(
            self.recalculate_markup_bottom_margin
        )
        self.bottom_margin_input.valueChanged.connect(
            self.recalculate_markup_head_size
        )

        self.top_margin_input.valueChanged.connect(
            self.recalculate_markup_head_size
        )

        size_row.addWidget(self.face_size_input)

        markup_layout.addLayout(size_row)

        self.margin_fit_status = QLabel("Поля ещё не применены")
        markup_layout.addWidget(self.margin_fit_status)

        # Четвёртый ряд: блокировка композиции.
        self.lock_composition_button = QPushButton(
            "Всё, успокойся, лучше уже не сделаешь"
        )
        self.lock_composition_button.clicked.connect(
            self.lock_composition
        )
        markup_layout.addWidget(self.lock_composition_button)

        # Пятый ряд: разблокировка композиции.
        self.unlock_composition_button = QPushButton(
            "Вернуть как было до того, как я решил, что лучше уже не сделаю"
        )
        self.unlock_composition_button.clicked.connect(
            self.unlock_composition
        )
        markup_layout.addWidget(self.unlock_composition_button)

        self.unlock_composition_button.setEnabled(False)


        # -------------------------------------------------
        # АДАПТАЦИЯ ШИРИНЫ ПАНЕЛИ «РАЗМЕТКА»
        # Длинный текст не должен растягивать панель.
        # Полный текст показывается при наведении.
        # -------------------------------------------------

        self.markup_group.setMinimumWidth(0)

        # Разрешаем панели сжиматься по горизонтали.
        group_policy = self.markup_group.sizePolicy()
        group_policy.setHorizontalPolicy(
            QSizePolicy.Policy.Ignored
        )
        self.markup_group.setSizePolicy(group_policy)

        # Убираем ограничения ширины у кнопок и подписей.
        for widget in self.markup_group.findChildren(QWidget):

            if isinstance(widget, (QPushButton, QLabel)):
                widget.setMinimumWidth(0)

                widget_policy = widget.sizePolicy()
                widget_policy.setHorizontalPolicy(
                    QSizePolicy.Policy.Ignored
                )
                widget.setSizePolicy(widget_policy)

                # Полная надпись доступна при наведении.
                widget.setToolTip(widget.text())

            elif isinstance(widget, QDoubleSpinBox):
                # Сохраняем минимальную удобную ширину полей.
                widget.setMinimumWidth(55)


        self.markup_dock = QDockWidget("Разметка", self)
        self.markup_dock.setObjectName("markup_dock")
        self.markup_dock.setAllowedAreas(
            Qt.DockWidgetArea.AllDockWidgetAreas
        )
        self.markup_dock.setWidget(self.markup_group)
        self.addDockWidget(
            Qt.DockWidgetArea.BottomDockWidgetArea,
            self.markup_dock
        )
        self.markup_dock.hide()
        
        # -------------------------------------------------
        # ПАНЕЛЬ ОБРАБОТКИ ФОТОГРАФИИ
        # -------------------------------------------------

        self.processing_panel = QWidget()
        processing_layout = QVBoxLayout(self.processing_panel)

        processing_title = QLabel("Инструменты обработки")
        processing_layout.addWidget(processing_title)

        # Пока добавляем первый инструмент — удаление фона.
        background_group = QGroupBox("Удаление фона")
        background_layout = QVBoxLayout(background_group)

        self.background_color_button = QPushButton(
            "Выбрать цвет исходного фона"
        )
        self.background_color_button.clicked.connect(
            self.choose_background_color
        )
        background_layout.addWidget(self.background_color_button)

        # -------------------------------------------------
        # ЦВЕТ ДЛЯ ЗАМЕНЫ УДАЛЁННОГО ФОНА
        # -------------------------------------------------

        replacement_color_title = QLabel(
            "Цвет для замены фона:"
        )
        background_layout.addWidget(replacement_color_title)

        # Пять образцов цвета нового фона и выбор произвольного цвета.
        replacement_colors_row = QHBoxLayout()
        self.background_replacement_color_button = QPushButton(
            "Свой цвет"
        )
        self.background_replacement_color_button.setToolTip(
            "Выбрать произвольный цвет с экрана"
        )
        self.background_replacement_color_button.clicked.connect(
            self.choose_background_replacement_color
        )
        replacement_colors_row.addWidget(
            self.background_replacement_color_button
        )

        self.background_replacement_color_group = QButtonGroup(self)
        # The active sample is set by _update_background_replacement_color_controls,
        # including clearing every sample for a custom color.
        self.background_replacement_color_group.setExclusive(False)
        self.background_replacement_color_buttons = []

        replacement_color_presets = (
            ("Белый", "#FFFFFF", "replacement_white_button"),
            ("Серый", "#D9D9D9", "replacement_gray_button"),
            ("Серо-голубой", "#C8DCE8", "replacement_blue_gray_button"),
            ("Светло-лиловый", "#F0E2FC", "replacement_lilac_button"),
            ("Светло-голубой", "#C4F4FF", "replacement_light_blue_button"),
        )

        for color_name, color_value, attribute_name in replacement_color_presets:
            button = QPushButton()
            button.setFixedSize(25, 24)
            button.setCheckable(True)
            button.setToolTip(f"{color_name} ({color_value})")
            button.setAccessibleName(f"Цвет фона: {color_name}")
            button.setStyleSheet(
                f"QPushButton {{ background-color: {color_value}; "
                "border: 1px solid #777; border-radius: 2px; }"
                "QPushButton:checked { border: 2px solid #218838; }"
            )
            button.clicked.connect(
                lambda checked=False, selected_color=color_value:
                    self.set_background_replacement_color(selected_color)
            )
            self.background_replacement_color_group.addButton(button)
            self.background_replacement_color_buttons.append(
                (QColor(color_value), button)
            )
            setattr(self, attribute_name, button)
            replacement_colors_row.addWidget(button)

        background_layout.addLayout(replacement_colors_row)
        self._update_background_replacement_color_controls()

        # Первый параметр — порог по цвету RGB.
        self.background_threshold_label = QLabel("Порог по цвету: 30")
        background_layout.addWidget(self.background_threshold_label)

        self.background_threshold = QSlider(
            Qt.Orientation.Horizontal
        )
        self.background_threshold.setRange(1, 150)
        self.background_threshold.setValue(30)

        self.background_threshold.valueChanged.connect(
            lambda value: self.background_threshold_label.setText(
                f"Порог по цвету: {value}"
            )
        )

        background_layout.addWidget(self.background_threshold)

        # Второй параметр — порог по оттенку HSV.
        # 100% означает, что оттенок не ограничивается.
        self.background_hue_threshold_label = QLabel(
            "Порог по оттенку: 100%"
        )
        background_layout.addWidget(
            self.background_hue_threshold_label
        )

        self.background_hue_threshold = QSlider(
            Qt.Orientation.Horizontal
        )
        self.background_hue_threshold.setRange(0, 100)
        self.background_hue_threshold.setValue(100)

        self.background_hue_threshold.valueChanged.connect(
            lambda value: self.background_hue_threshold_label.setText(
                f"Порог по оттенку: {value}%"
            )
        )

        background_layout.addWidget(
            self.background_hue_threshold
        )

        # Смягчаем край уже построенной/отредактированной маски.
        self.background_border_label = QLabel("Граница: 0%")
        background_layout.addWidget(self.background_border_label)

        self.background_border_slider = QSlider(
            Qt.Orientation.Horizontal
        )
        self.background_border_slider.setRange(0, 100)
        self.background_border_slider.setValue(0)
        self._background_border_slider_dragging = False
        self.background_border_preview_timer = QTimer(self)
        self.background_border_preview_timer.setSingleShot(True)
        self.background_border_preview_timer.setInterval(100)
        self.background_border_preview_timer.timeout.connect(
            self._refresh_background_border_preview
        )
        self.background_border_slider.sliderPressed.connect(
            self._on_background_border_slider_pressed
        )
        self.background_border_slider.sliderReleased.connect(
            self._on_background_border_slider_released
        )
        self.background_border_slider.valueChanged.connect(
            self.on_background_border_changed
        )
        background_layout.addWidget(self.background_border_slider)

        self.background_apply_button = QPushButton("Удалить фон")
        self.background_apply_button.clicked.connect(
            self.remove_background
        )
        background_layout.addWidget(self.background_apply_button)

        # -------------------------------------------------
        # РУЧНАЯ КОРРЕКЦИЯ МАСКИ УДАЛЕНИЯ ФОНА
        # -------------------------------------------------

        brush_group = QGroupBox("Ручная коррекция")
        brush_layout = QVBoxLayout(brush_group)

        # Предпросмотр внешнего вида и размера кисти.
        brush_preview_title = QLabel("Предпросмотр кисти:")
        brush_layout.addWidget(brush_preview_title)

        self.background_brush_preview = QLabel()
        self.background_brush_preview.setFixedSize(180, 100)
        brush_layout.addWidget(self.background_brush_preview)

        # Выбор типа кисти.
        self.background_paint_button = QPushButton("Кисть окраски")
        self.background_paint_button.setCheckable(True)
        self.background_paint_button.clicked.connect(
            lambda checked: self.set_background_brush_type(
                "paint", checked
            )
        )
        brush_layout.addWidget(self.background_paint_button)

        self.background_erase_button = QPushButton("Кисть отмены")
        self.background_erase_button.setCheckable(True)
        self.background_erase_button.clicked.connect(
            lambda checked: self.set_background_brush_type(
                "erase", checked
            )
        )
        brush_layout.addWidget(self.background_erase_button)

        # Размер кисти.
        self.background_brush_size_label = QLabel("Размер кисти: 30")
        brush_layout.addWidget(self.background_brush_size_label)

        self.background_brush_size_slider = QSlider(
            Qt.Orientation.Horizontal
        )
        self.background_brush_size_slider.setRange(1, 200)
        self.background_brush_size_slider.setValue(30)
        self.background_brush_size_slider.valueChanged.connect(
            self.update_background_brush_settings
        )
        brush_layout.addWidget(self.background_brush_size_slider)

        # Мягкость края кисти.
        self.background_brush_softness_label = QLabel(
            "Мягкость кисти: 50%"
        )
        brush_layout.addWidget(
            self.background_brush_softness_label
        )

        self.background_brush_softness_slider = QSlider(
            Qt.Orientation.Horizontal
        )
        self.background_brush_softness_slider.setRange(0, 100)
        self.background_brush_softness_slider.setValue(50)
        self.background_brush_softness_slider.valueChanged.connect(
            self.update_background_brush_settings
        )
        brush_layout.addWidget(
            self.background_brush_softness_slider
        )

        background_layout.addWidget(brush_group)        

        processing_layout.addWidget(background_group)
        processing_layout.addStretch()

        self.processing_dock = QDockWidget("Обработка", self)
        self.processing_dock.setObjectName("processing_dock")
        self.processing_dock.setAllowedAreas(
            Qt.DockWidgetArea.AllDockWidgetAreas
        )

        # Устанавливаем начальные настройки предпросмотра кисти.
        self.update_background_brush_settings()

        self.processing_dock.setWidget(self.processing_panel)

        self.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            self.processing_dock
        )

        self.processing_dock.hide()        
        
        view_menu = self.menuBar().addMenu("Вид")
        view_menu.addAction(self.files_dock.toggleViewAction())
        view_menu.addAction(self.format_dock.toggleViewAction())
        view_menu.addAction(self.markup_dock.toggleViewAction())  
        view_menu.addAction(self.processing_dock.toggleViewAction())        
        self.statusBar().showMessage("Готово")
        # -------------------------------------------------
        # ГОРЯЧИЕ КЛАВИШИ ДЛЯ РАБОТЫ С ФОТОГРАФИЯМИ
        # -------------------------------------------------

        # Ctrl+C — копировать выбранные фотографии.
        self.shortcut_copy = QShortcut(
            QKeySequence("Ctrl+C"), self
        )
        self.shortcut_copy.activated.connect(
            lambda: self.copy_selected_photos()
            if self.photo_grid_has_focus() else None
        )

        # Ctrl+X — вырезать выбранные фотографии.
        self.shortcut_cut = QShortcut(
            QKeySequence("Ctrl+X"), self
        )
        self.shortcut_cut.activated.connect(
            lambda: self.cut_selected_photos()
            if self.photo_grid_has_focus() else None
        )

        # Ctrl+V — вставить фотографии в текущую папку.
        self.shortcut_paste = QShortcut(
            QKeySequence("Ctrl+V"), self
        )
        self.shortcut_paste.activated.connect(
            lambda: self.paste_photos()
            if self.photo_grid_has_focus() else None
        )

        # Delete — удалить выбранные фотографии.
        self.shortcut_delete = QShortcut(
            QKeySequence("Delete"), self
        )
        self.shortcut_delete.activated.connect(
            lambda: self.delete_selected_photos()
            if self.photo_grid_has_focus() else None
        )

        # F2 — переименовать выбранную фотографию.
        # Сочетание работает, когда фокус находится в сетке фотографий.
        self.shortcut_rename = QShortcut(
            QKeySequence("F2"), self.files_list
        )
        self.shortcut_rename.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.shortcut_rename.activated.connect(
            self.rename_selected_photo
        )

    def on_mode_combo_changed(self, text):
        if text == "Разметка":
            self.change_mode("markup")
        elif text == "Обработка":
            self.change_mode("processing")
        else:
            self.change_mode("formats")

    def change_mode(self, mode):
        # Зафиксированную композицию можно вернуть в разметку только
        # через unlock_composition(), который сначала восстанавливает её состояние.
        if mode == "markup" and self.photo_view.composition_locked:
            mode = "processing"
        # В режиме обработки оставляем вкладку разметки доступной,
        # но выбранной делаем вкладку обработки.
        self.photo_view.set_markup_enabled(False)
        if mode == "processing":
            self.markup_dock.show()
        else:
            self.markup_dock.hide()

        if hasattr(self, "processing_dock"):
            self.processing_dock.hide()

        if mode == "markup":
            self.markup_dock.show()
            self.photo_view.set_markup_enabled(True)
            self.statusBar().showMessage(
                "Разметка: настройка положения рамки и отметок"
            )
            target_text = "Разметка"

        elif mode == "processing":
            if hasattr(self, "processing_dock"):
                self.processing_dock.show()
                self.processing_dock.raise_()
            self.statusBar().showMessage(
                "Режим обработки фотографий"
            )
            target_text = "Обработка"

        else:
            self.statusBar().showMessage(
                "Режим выбора форматов"
            )
            target_text = "Форматы фотографий"

        # Синхронизируем верхний переключатель с текущим режимом.
        if self.mode_combo.currentText() != target_text:
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentText(target_text)
            self.mode_combo.blockSignals(False)
    def choose_background_color(self):
        # Запускаем глобальную пипетку, которая позволяет выбрать
        # цвет не только в PhotoStudio, но и в любом месте экрана.
        self.screen_color_picker = ScreenColorPicker()

        # Передаём выбранный цвет в метод сохранения.
        self.screen_color_picker.colorSelected.connect(
            self.set_background_color
        )

        # Показываем пипетку поверх остальных окон.
        self.screen_color_picker.start_picker()

    def _refresh_document_from_processing_result(self, result_pixmap):
        """Sync a legacy tool result into the transitional document layer."""
        if self.photo_document is None:
            return result_pixmap

        layer_ids = {
            layer.layer_id for layer in self.photo_document.layers
        }
        if {"background-fill", "working-image"}.issubset(layer_ids):
            # Layer-based results are already rendered; keep source pixels.
            return QPixmap.fromImage(
                self.composite_renderer.render(self.photo_document)
            )

        self.photo_document.replace_layer_image(
            "working-image", result_pixmap.toImage()
        )
        rendered = self.composite_renderer.render(self.photo_document)
        return QPixmap.fromImage(rendered)

    def set_background_replacement_color(self, color):
        # Сохраняем выбранный цвет нового фона.
        self.background_replacement_color = QColor(color)
        self._update_background_replacement_color_controls()

        # Если маска уже создана, сразу обновляем фотографию.
        if (
            self.background_mask_edit is not None
            and self.background_source_pixmap is not None
        ):
            result_pixmap = self.create_background_replacement_pixmap()

            if result_pixmap is not None:
                result_pixmap = self._refresh_document_from_processing_result(result_pixmap)
                self.current_pixmap = result_pixmap
                self.working_pixmap = result_pixmap.copy()

                self.photo_view.update_photo_pixels(result_pixmap)
                self.update_file_preview(result_pixmap)

                self.photo_view.background_mask = (
                    self.background_feathered_mask.copy()
                )
                self.photo_view.update()

        self.statusBar().showMessage(
            f"Цвет замены фона: "
            f"{self.background_replacement_color.name()}"
        )

    def _update_background_replacement_color_controls(self):
        """Show the active preset and the current custom color sample."""
        color = QColor(
            getattr(self, "background_replacement_color", QColor("#FFFFFF"))
        )

        for preset_color, button in getattr(
            self, "background_replacement_color_buttons", []
        ):
            button.setChecked(color == preset_color)

        if not hasattr(self, "background_replacement_color_button"):
            return

        color_sample = QPixmap(18, 18)
        color_sample.fill(color)
        painter = QPainter(color_sample)
        painter.setPen(QPen(QColor("#666666")))
        painter.drawRect(0, 0, 17, 17)
        painter.end()

        self.background_replacement_color_button.setIcon(
            QIcon(color_sample)
        )
        self.background_replacement_color_button.setIconSize(QSize(18, 18))

    def _set_background_mask_edit(self, mask, changed_rect=None):
        """Set M_edit while keeping the legacy mask name synchronized."""
        if mask is None:
            self.background_mask_edit = None
            self.background_mask = None
            self.background_feathered_mask = None
            self.background_mask_feather.reset()
            if hasattr(self, "background_border_preview_timer"):
                self.background_border_preview_timer.stop()
            if hasattr(self, "background_border_slider"):
                self.background_border_slider.blockSignals(True)
                self.background_border_slider.setValue(0)
                self.background_border_slider.blockSignals(False)
                self.background_border_label.setText("Граница: 0%")
            return

        edit_mask = np.ascontiguousarray(
            np.asarray(mask, dtype=np.uint8)
        ).copy()
        if edit_mask.ndim != 2:
            raise ValueError("Background edit mask must be two-dimensional")

        self.background_mask_edit = edit_mask
        self.background_mask = self.background_mask_edit
        self.background_feathered_mask = None

        if changed_rect is not None and self.background_mask_feather.ready:
            self.background_mask_feather.update_region(
                self.background_mask_edit,
                changed_rect,
                self.background_protected_mask,
            )
        else:
            self.background_mask_feather.reset()

    def _get_background_feathered_mask(self):
        """Return a fresh mask derived from M_edit and the slider value."""
        if self.background_mask_edit is None:
            self.background_feathered_mask = None
            return None

        percentage = (
            self.background_border_slider.value()
            if hasattr(self, "background_border_slider")
            else 0
        )
        self.background_feathered_mask = self.background_mask_feather.apply(
            self.background_mask_edit,
            percentage,
            self.background_protected_mask,
        )
        return self.background_feathered_mask

    def on_background_border_changed(self, value):
        """Re-render from M_edit; this does not run color segmentation."""
        self.background_border_label.setText(f"Граница: {int(value)}%")
        if (
            self.background_mask_edit is None
            or self.background_source_pixmap is None
        ):
            return

        if not self._background_border_slider_dragging:
            self.background_border_preview_timer.start()

    def _on_background_border_slider_pressed(self):
        self._background_border_slider_dragging = True
        self.background_border_preview_timer.stop()

    def _on_background_border_slider_released(self):
        self._background_border_slider_dragging = False
        if (
            self.background_mask_edit is not None
            and self.background_source_pixmap is not None
        ):
            self.background_border_preview_timer.start()

    def _refresh_background_border_preview(self):
        """Render the latest slider value once after rapid changes settle."""
        if (
            self.background_mask_edit is None
            or self.background_source_pixmap is None
        ):
            return

        feathered_mask = self._get_background_feathered_mask()
        if feathered_mask is None:
            return

        result_pixmap = self.create_background_replacement_pixmap()
        if result_pixmap is None:
            return

        result_pixmap = self._refresh_document_from_processing_result(
            result_pixmap
        )
        self.current_pixmap = result_pixmap
        self.working_pixmap = result_pixmap.copy()
        self.photo_view.update_photo_pixels(result_pixmap)
        self.photo_view.background_mask = feathered_mask.copy()
        self.photo_view.update()
        self.update_file_preview(result_pixmap)

    def choose_background_replacement_color(self):
        # Запускаем отдельную глобальную пипетку
        # для выбора цвета нового фона.
        self.screen_replacement_color_picker = ScreenColorPicker()

        self.screen_replacement_color_picker.colorSelected.connect(
            self.set_background_replacement_color
        )

        self.screen_replacement_color_picker.start_picker()

    def set_background_color(self, color):
        # Сохраняем цвет, выбранный глобальной пипеткой.
        self.background_color = color

        # Показываем выбранный цвет в строке состояния.
        self.statusBar().showMessage(
            f"Выбран цвет фона: {color.name()}"
        )

    def set_background_brush_type(self, brush_type, enabled):
        # Включаем или выключаем выбранную кисть.
        self.photo_view.background_brush_enabled = enabled

        if enabled:
            self.photo_view.background_brush_type = brush_type

        # Одновременно активной может быть только одна кисть.
        self.background_paint_button.setChecked(
            enabled and brush_type == "paint"
        )
        self.background_erase_button.setChecked(
            enabled and brush_type == "erase"
        )

        # При включении кисти используем перекрестный курсор.
        if enabled:
            self.photo_view.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.photo_view.setCursor(Qt.CursorShape.ArrowCursor)

    def update_background_brush_settings(self, value=None):
        # Получаем текущие настройки кисти из ползунков.
        size = self.background_brush_size_slider.value()
        softness = self.background_brush_softness_slider.value()

        self.background_brush_size_label.setText(
            f"Размер кисти: {size}"
        )

        self.background_brush_softness_label.setText(
            f"Мягкость кисти: {softness}%"
        )

        # Передаём настройки в рабочую область фотографии.
        self.photo_view.background_brush_size = size
        self.photo_view.background_brush_softness = softness

        # Обновляем окно предпросмотра.
        self.update_background_brush_preview()

    def update_background_brush_preview(self):
        # Создаём изображение для окна предпросмотра.
        preview = QPixmap(180, 100)
        preview.fill(QColor("#303030"))

        painter = QPainter(preview)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing
        )

        size = self.background_brush_size_slider.value()
        softness = self.background_brush_softness_slider.value()

        # Переводим размер кисти в размер круга предпросмотра.
        radius = max(3, min(42, size * 0.4))

        center_x = 90
        center_y = 50

        # Рисуем мягкий край несколькими полупрозрачными кругами.
        inner_radius = radius * (1 - softness / 100 * 0.65)

        for step in range(20, 0, -1):
            factor = step / 20
            current_radius = inner_radius + (
                radius - inner_radius
            ) * factor

            alpha = int(
                255 * math.exp(-((1 - factor) * 3) ** 2)
            )

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(
                QColor(255, 255, 255, alpha)
            )

            painter.drawEllipse(
                QRectF(
                    center_x - current_radius,
                    center_y - current_radius,
                    current_radius * 2,
                    current_radius * 2
                )
            )

        painter.end()

        self.background_brush_preview.setPixmap(preview)

    def create_background_replacement_pixmap(self):
        # Проверяем наличие исходной фотографии и маски.
        if (
            self.background_source_pixmap is None
            or self.background_mask_edit is None
        ):
            return None

        render_mask = self._get_background_feathered_mask()
        if render_mask is None:
            return None

        layer_ids = (
            {layer.layer_id for layer in self.photo_document.layers}
            if self.photo_document is not None
            else set()
        )

        if {"background-fill", "working-image"}.issubset(layer_ids):
            # Keep the subject pixels, its mask and the replacement fill apart.
            self.photo_document.replace_layer_mask(
                "working-image",
                self._grayscale_qimage_from_array(render_mask),
            )

            fill_image = QImage(
                render_mask.shape[1],
                render_mask.shape[0],
                QImage.Format.Format_RGBA8888,
            )
            fill_image.fill(self.background_replacement_color)
            self.photo_document.replace_layer_image(
                "background-fill",
                fill_image,
            )

            return QPixmap.fromImage(
                self.composite_renderer.render(self.photo_document)
            )

        # Получаем исходные пиксели фотографии.
        image = self.background_source_pixmap.toImage().convertToFormat(
            QImage.Format.Format_RGBA8888
        )

        width = image.width()
        height = image.height()

        pixels = np.frombuffer(
            image.bits(),
            dtype=np.uint8,
            count=image.sizeInBytes()
        ).reshape((height, width, 4)).copy()

        # Переводим маску в диапазон от 0 до 1.
        alpha = render_mask.astype(np.float32) / 255.0

        # Получаем выбранный цвет нового фона.
        color = self.background_replacement_color

        replacement = np.array(
            [color.red(), color.green(), color.blue()],
            dtype=np.float32
        )

        # Смешиваем исходную фотографию с новым фоном.
        # Белая часть маски сохраняет человека.
        # Чёрная часть маски заменяется новым цветом.
        original_rgb = pixels[:, :, :3].astype(np.float32)

        result_rgb = (
            original_rgb * alpha[:, :, None]
            + replacement[None, None, :] * (1 - alpha[:, :, None])
        )

        pixels[:, :, :3] = np.clip(
            result_rgb, 0, 255
        ).astype(np.uint8)

        # Итоговое изображение полностью непрозрачное.
        pixels[:, :, 3] = 255

        result_image = QImage(
            pixels.data,
            width,
            height,
            pixels.strides[0],
            QImage.Format.Format_RGBA8888
        ).copy()

        return QPixmap.fromImage(result_image)

    def apply_background_brush(
        self,
        start_pos,
        end_pos,
        brush_type,
        brush_size,
        brush_softness
    ):
        # Проверяем, создана ли маска удаления фона.
        if (
            self.background_mask_edit is None
            or self.background_source_pixmap is None
        ):
            return

        height, width = self.background_mask_edit.shape

        # Создаём отдельный слой для мазка кисти.
        brush_layer = np.zeros(
            (height, width),
            dtype=np.uint8
        )

        # Размер кисти задаётся как диаметр.
        radius = max(1, int(brush_size / 2))

        # Переводим координаты мыши в целые координаты изображения.
        end_x = int(round(end_pos.x()))
        end_y = int(round(end_pos.y()))

        if start_pos is None:
            # Первый щелчок — круглый отпечаток кисти.
            cv2.circle(
                brush_layer,
                (end_x, end_y),
                radius,
                255,
                -1
            )
        else:
            start_x = int(round(start_pos.x()))
            start_y = int(round(start_pos.y()))

            # Соединяем точки, чтобы мазок был непрерывным.
            cv2.line(
                brush_layer,
                (start_x, start_y),
                (end_x, end_y),
                255,
                thickness=radius * 2
            )

        # Мягкость регулирует размытие края кисти.
        softness = max(0, min(100, int(brush_softness)))

        if softness > 0:
            sigma = max(0.1, radius * softness / 100)

            brush_layer = cv2.GaussianBlur(
                brush_layer,
                (0, 0),
                sigmaX=sigma,
                sigmaY=sigma
            )

        # Защищённый уголок не меняется от ручных мазков.
        if (
            self.background_protected_mask is not None
            and self.background_protected_mask.shape == brush_layer.shape
        ):
            brush_layer[self.background_protected_mask > 0] = 0

        # Кисть окраски возвращает объект на фотографию.
        if brush_type == "paint":
            edited_mask = np.maximum(
                self.background_mask_edit,
                brush_layer
            )

        # Кисть отмены удаляет объект из маски.
        elif brush_type == "erase":
            edited_mask = np.minimum(
                self.background_mask_edit,
                255 - brush_layer
            )
        else:
            return

        if (
            self.background_protected_mask is not None
            and self.background_protected_mask.shape == edited_mask.shape
        ):
            edited_mask = np.maximum(
                edited_mask,
                self.background_protected_mask
            )

        brush_x, brush_y, brush_width, brush_height = cv2.boundingRect(
            brush_layer
        )
        changed_rect = (
            (brush_x, brush_y, brush_width, brush_height)
            if brush_width > 0 and brush_height > 0
            else None
        )
        self._set_background_mask_edit(
            edited_mask,
            changed_rect=changed_rect,
        )

        # Передаём обновлённую маску в область фотографии.
        feathered_mask = self._get_background_feathered_mask()
        self.photo_view.background_mask = feathered_mask.copy()
        self.photo_view.update()

        # Создаём результат с выбранным цветом нового фона.
        result_pixmap = self.create_background_replacement_pixmap()


        if result_pixmap is None:
            return

        result_pixmap = self._refresh_document_from_processing_result(result_pixmap)
        self.current_pixmap = result_pixmap
        self.working_pixmap = result_pixmap.copy()

        # Обновляем фотографию без сброса кадрирования и масштаба.
        self.photo_view.update_photo_pixels(result_pixmap)

        # Обновляем маску для визуального отображения.
        self.photo_view.background_mask = (
            self.background_feathered_mask.copy()
        )
        self.photo_view.update()

        # Обновляем миниатюру.
        self.update_file_preview(result_pixmap)

    def remove_background(self):
        # Проверяем наличие рабочей копии для обработки.
        if (
            self.working_pixmap is None
            or self.working_pixmap.isNull()
        ):
            QMessageBox.warning(
                self,
                "Удаление фона",
                "Сначала нажмите «Готово» в режиме разметки."
            )
            return

        # Проверяем, выбран ли цвет фона.
        if self.background_color is None:
            QMessageBox.warning(
                self,
                "Удаление фона",
                "Сначала выберите цвет фона."
            )
            return

        # Сохраняем чистую рабочую копию до обработки фона.
        # Повторные запуски используют её, не накапливая изменения.
        if self.background_source_pixmap is None:
            if self.photo_document is not None:
                source_layer = self.photo_document.get_layer("working-image")
                self.background_source_pixmap = QPixmap.fromImage(
                    source_layer.image
                )
            else:
                self.background_source_pixmap = (
                    self.working_pixmap.copy()
                )

        image = self.background_source_pixmap.toImage().convertToFormat(
            QImage.Format.Format_RGBA8888
        )

        width = image.width()
        height = image.height()

        # Копируем пиксели в массив OpenCV.
        pointer = image.bits()
        pixels = np.frombuffer(
            pointer,
            dtype=np.uint8,
            count=image.sizeInBytes()
        ).reshape((height, width, 4)).copy()

        # Получаем выбранный цвет.
        color = self.background_color
        target_color = np.array(
            [color.red(), color.green(), color.blue()],
            dtype=np.int32
        )

        # Сравниваем цвет каждого пикселя с выбранным цветом.
        # int32 предотвращает переполнение при возведении разности в квадрат.
        rgb = pixels[:, :, :3].astype(np.int32)

        difference = np.sqrt(
            np.sum((rgb - target_color) ** 2, axis=2)
        )

        # Первый параметр — допустимое отличие цвета RGB.
        threshold = self.background_threshold.value()

        # Переводим фотографию в цветовую модель HSV.
        hsv_pixels = cv2.cvtColor(
            pixels[:, :, :3],
            cv2.COLOR_RGB2HSV
        )

        # Получаем оттенок выбранного пипеткой цвета.
        target_rgb = np.uint8([[
            [color.red(), color.green(), color.blue()]
        ]])

        target_hsv = cv2.cvtColor(
            target_rgb,
            cv2.COLOR_RGB2HSV
        )

        target_hue = int(target_hsv[0, 0, 0])

        # Считаем разницу оттенков с учётом перехода
        # через границу цветового круга.
        pixel_hue = hsv_pixels[:, :, 0].astype(np.int16)

        hue_difference = np.abs(pixel_hue - target_hue)
        hue_difference = np.minimum(
            hue_difference,
            180 - hue_difference
        )

        # Второй параметр — допустимое отличие оттенка.
        # Максимальная разница оттенков в OpenCV равна 90.
        hue_threshold = (
            self.background_hue_threshold.value() * 0.9
        )

        hue_mask = hue_difference <= hue_threshold

        # Для серых и почти серых цветов оттенок ненадёжен.
        # Поэтому не ограничиваем такие пиксели по Hue.
        low_saturation = (
            (hsv_pixels[:, :, 1] < 15)
            | (int(target_hsv[0, 0, 1]) < 15)
        )

        hue_mask = hue_mask | low_saturation

        # Удаляем только пиксели, прошедшие оба условия.
        color_mask = (
            (difference <= threshold) & hue_mask
        ).astype(np.uint8)

        # Оставляем только области, соединённые с краями фотографии.
        count, labels = cv2.connectedComponents(color_mask)

        background_mask = np.zeros(
            (height, width),
            dtype=np.uint8
        )

        edge_labels = np.unique(
            np.concatenate([
                labels[0, :],
                labels[-1, :],
                labels[:, 0],
                labels[:, -1]
            ])
        )

        for label in edge_labels:
            if label != 0:
                background_mask[labels == label] = 255

        # Создаём общую маску объекта.
        # 255 — оставить пиксель фотографии.
        # 0 — сделать пиксель прозрачным.
        editable_mask = pixels[:, :, 3].copy()

        # Автоматически удаляем найденный фон из маски.
        editable_mask[background_mask == 255] = 0

        # Уголок остаётся сохранённым в маске и на отдельном белом слое.
        if (
            self.background_protected_mask is not None
            and self.background_protected_mask.shape == editable_mask.shape
        ):
            editable_mask = np.maximum(
                editable_mask,
                self.background_protected_mask
            )

        self._set_background_mask_edit(editable_mask)

        # Передаём начальную маску в область фотографии.
        feathered_mask = self._get_background_feathered_mask()
        self.photo_view.background_mask = feathered_mask.copy()

        # Создаём фотографию с выбранным цветом нового фона.
        result_pixmap = self.create_background_replacement_pixmap()

        if result_pixmap is None:
            return

        # Сохраняем результат обработки.
        result_pixmap = self._refresh_document_from_processing_result(result_pixmap)
        self.current_pixmap = result_pixmap
        self.working_pixmap = result_pixmap.copy()

        # Обновляем пиксели без сброса кадрирования и разметки.
        self.photo_view.update_photo_pixels(result_pixmap)

        # Обновляем миниатюру фотографии.
        self.update_file_preview(result_pixmap)

        self.statusBar().showMessage(
            "Удаление фона завершено. Исходный файл не изменён."
        )

    def align_photo_by_eyes(self):
        view = self.photo_view
        if view.left_eye is None or view.right_eye is None:
            QMessageBox.warning(
                self,
                "Выравнивание по глазам",
                "Сначала отметьте оба глаза."
            )
            return
        if math.hypot(
            view.right_eye.x() - view.left_eye.x(),
            view.right_eye.y() - view.left_eye.y()
        ) < 1:
            QMessageBox.warning(
                self,
                "Выравнивание по глазам",
                "Метки глаз слишком близко друг к другу. Поставьте их точнее."
            )
            return
        if view.align_eyes():
            self.statusBar().showMessage("Фотография выровнена по глазам.")

    def align_photo_by_vertical_axis(self):
        view = self.photo_view
        if view.head_top is None or view.chin is None:
            QMessageBox.warning(
                self,
                "Выравнивание по вертикальной оси",
                "Сначала отметьте макушку и подбородок."
            )
            return
        if math.hypot(
            view.chin.x() - view.head_top.x(),
            view.chin.y() - view.head_top.y()
        ) < 1:
            QMessageBox.warning(
                self,
                "Выравнивание по вертикальной оси",
                "Метки макушки и подбородка слишком близко. Поставьте их точнее."
            )
            return
        if view.align_vertical_axis():
            self.statusBar().showMessage(
                "Фотография выровнена по вертикальной оси."
            )

    def set_markup_tool(self, tool):
        self.photo_view.set_tool(tool)
        if tool == "move":
            self.statusBar().showMessage(
                "Зажми левую кнопку мыши на рамке и перетащи её"
            )
        elif tool == "left_eye":
            self.statusBar().showMessage(
                "Щёлкни по левой точке горизонтали на фотографии"
            )
        elif tool == "right_eye":
            self.statusBar().showMessage(
                "Щёлкни по правой точке горизонтали на фотографии"
            )
        elif tool == "head_top":
            self.statusBar().showMessage(
                "Щёлкни по самой верхней внешней границе причёски"
            )
        elif tool == "chin":
            self.statusBar().showMessage("Щёлкни по самой нижней точке подбородка")

    def apply_face_margins(self):
        view = self.photo_view

        if view.pixmap.isNull():
            QMessageBox.warning(
                self,
                "Нет фотографии",
                "Сначала открой фотографию."
            )
            return

        if view.head_top is None or view.chin is None:
            QMessageBox.warning(
                self,
                "Не хватает меток",
                "Сначала отметь верх головы и подбородок."
            )
            return

        top_mm = self.top_margin_input.value()
        bottom_mm = self.bottom_margin_input.value()

        format_height = view.format_height_mm
        format_width = view.format_width_mm

        markup_type = getattr(
            self,
            "current_markup_type",
            "Разметка по двум точкам"
        )

        image_width = view.pixmap.width()
        image_height = view.pixmap.height()

        if markup_type == "Разметка по трём точкам (ГОСТ)":

            if view.left_eye is None or view.right_eye is None:
                QMessageBox.warning(
                    self,
                    "Не хватает меток",
                    "Для разметки ГОСТ отметь левый и правый глаз."
                )
                return

            face_size = self.face_size_input.value()

            if face_size <= 0:
                QMessageBox.warning(
                    self,
                    "Не задан размер лицевой части",
                    "Укажи размер лицевой части в настройках формата."
                )
                return

            eye_y = (
                view.left_eye.y() + view.right_eye.y()
            ) / 2

            face_height = view.chin.y() - eye_y

            if face_height <= 0:
                QMessageBox.warning(
                    self,
                    "Неверные метки",
                    "Уровень глаз должен находиться выше подбородка."
                )
                return

            scale = face_height / face_size

            crop_height = format_height * scale
            crop_width = crop_height * format_width / format_height

            center_x = (
                view.head_top.x() + view.chin.x()
            ) / 2

            crop_x = center_x - crop_width / 2
            crop_y = view.head_top.y() - top_mm * scale

            bottom_mm = 0

            points_to_check = (
                view.head_top,
                view.chin,
                view.left_eye,
                view.right_eye
            )

        else:
            usable_height = (
                format_height - top_mm - bottom_mm
            )

            if usable_height <= 0:
                QMessageBox.warning(
                    self,
                    "Неверные поля",
                    "Сумма верхнего и нижнего полей должна быть "
                    "меньше высоты формата."
                )
                return

            face_height = view.chin.y() - view.head_top.y()

            if face_height <= 0:
                QMessageBox.warning(
                    self,
                    "Неверные метки",
                    "Подбородок должен находиться ниже макушки."
                )
                return

            crop_height = face_height * format_height / usable_height
            crop_width = crop_height * format_width / format_height

            center_x = (
                view.head_top.x() + view.chin.x()
            ) / 2

            crop_x = center_x - crop_width / 2
            crop_y = (
                view.head_top.y()
                - crop_height * top_mm / format_height
            )

            points_to_check = (
                view.head_top,
                view.chin
            )

        for point in points_to_check:
            if not (
                0 <= point.x() <= image_width
                and 0 <= point.y() <= image_height
            ):
                QMessageBox.warning(
                    self,
                    "Метки вне фотографии",
                    "Одна из меток находится за границами изображения."
                )
                return
        
        print(
            f"DEBUG: фото {image_width}x{image_height}; "
            f"рамка x={crop_x:.1f}, y={crop_y:.1f}, "
            f"ширина={crop_width:.1f}, высота={crop_height:.1f}; "
            f"правая граница={crop_x + crop_width:.1f}, "
            f"нижняя граница={crop_y + crop_height:.1f}"
        )

        if (
            crop_x < 0
            or crop_y < 0
            or crop_x + crop_width > image_width
            or crop_y + crop_height > image_height
            ):
            pass

        view.crop_rect = QRectF(
            crop_x,
            crop_y,
            crop_width,
            crop_height
        )

        view.margin_fit_applied = True

        # После применения разметки фиксируем пропорции выбранного формата.
        view.crop_ratio_locked = True

        # Увеличиваем фотографию вместе с рамкой кадрирования.
        # Масштаб рассчитываем по высоте рамки, чтобы её верхняя
        # и нижняя границы максимально заполняли рабочую область.

        if view.fit_scale is None or view.fit_scale <= 0:
            image_width = view.pixmap.width()
            image_height = view.pixmap.height()
            area_width = max(1, view.width())
            area_height = max(1, view.height())

            if image_width > image_height:
                view.fit_scale = area_height / image_height
            else:
                view.fit_scale = min(
                    area_width / image_width,
                    area_height / image_height
                )

        # Оставляем небольшой отступ сверху и снизу.
        view.zoom = (
            view.height() * 0.92
        ) / (
            crop_height * view.fit_scale
        )

        # Центрируем именно рамку, а не всю фотографию.
        crop_center_x = crop_x + crop_width / 2
        crop_center_y = crop_y + crop_height / 2

        view.pan_offset = QPointF(
            view.zoom * (
                view.pixmap.width() * view.fit_scale / 2
                - crop_center_x * view.fit_scale
            ),
            view.zoom * (
                view.pixmap.height() * view.fit_scale / 2
                - crop_center_y * view.fit_scale
            )
        )

        view.update()

        unit_labels = {
            "mm": "мм",
            "in": "″",
            "px": "px"
        }
        unit_label = unit_labels.get(
            getattr(self, "current_format_units", "mm"), "ед."
        )

        self.margin_fit_status.setText(
            f"Поля применены: верхнее {top_mm:.1f} {unit_label}, "
            f"нижнее {bottom_mm:.1f} {unit_label}"
        )

        self.statusBar().showMessage(
            "Верхнее и нижнее поля применены."
        )

    def create_working_copy(self, include_corner=True):
        # Рамка задана в координатах текущего изображения в рабочей области.
        if (
            not hasattr(self, "photo_view")
            or self.photo_view.pixmap.isNull()
        ):
            return None

        source_pixmap = self.photo_view.pixmap
        crop = self.photo_view.crop_rect

        if crop.isEmpty() or crop.width() <= 0 or crop.height() <= 0:
            return None

        # Округляем границы наружу, сохраняя всю сформированную рамку.
        left = int(np.floor(crop.x()))
        top = int(np.floor(crop.y()))
        right = int(np.ceil(crop.x() + crop.width()))
        bottom = int(np.ceil(crop.y() + crop.height()))

        width = right - left
        height = bottom - top

        if width <= 0 or height <= 0:
            return None

        # Области рамки за пределами текущего изображения будут белыми.
        result = np.full(
            (height, width, 4),
            [255, 255, 255, 255],
            dtype=np.uint8
        )

        image = source_pixmap.toImage().convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        source_width = image.width()
        source_height = image.height()

        pixels = np.frombuffer(
            image.bits(),
            dtype=np.uint8,
            count=image.sizeInBytes()
        ).reshape((source_height, source_width, 4))

        source_left = max(0, left)
        source_top = max(0, top)
        source_right = min(source_width, right)
        source_bottom = min(source_height, bottom)

        if source_right > source_left and source_bottom > source_top:
            dest_left = source_left - left
            dest_top = source_top - top
            dest_right = dest_left + (source_right - source_left)
            dest_bottom = dest_top + (source_bottom - source_top)
            result[dest_top:dest_bottom, dest_left:dest_right] = pixels[
                source_top:source_bottom,
                source_left:source_right
            ]

        result_image = QImage(
            result.data,
            width,
            height,
            result.strides[0],
            QImage.Format.Format_RGBA8888
        ).copy()
        result_pixmap = QPixmap.fromImage(result_image)

        if include_corner:
            # Старый сценарий сохраняется для вызовов без параметра.
            painter = QPainter(result_pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            crop_in_result = QRectF(
                crop.x() - left,
                crop.y() - top,
                crop.width(),
                crop.height()
            )
            self.photo_view.draw_format_corner(painter, crop_in_result)
            painter.end()
        return result_pixmap
    @staticmethod
    def _grayscale_qimage_from_array(mask):
        """Make an independent grayscale QImage from a NumPy mask."""
        mask = np.ascontiguousarray(mask, dtype=np.uint8)
        height, width = mask.shape
        return QImage(
            mask.data,
            width,
            height,
            mask.strides[0],
            QImage.Format.Format_Grayscale8
        ).copy()

    def _make_protected_working_layers(self, working_pixmap, crop):
        """Build the source, outside-white and protected-corner layers."""
        width = working_pixmap.width()
        height = working_pixmap.height()

        background_image = QImage(
            width, height, QImage.Format.Format_RGBA8888
        )
        background_image.fill(Qt.GlobalColor.transparent)
        background_layer = PhotoLayer(
            background_image,
            name="Фон",
            layer_id="background-fill",
            metadata={"role": "replaceable-background"},
        )

        crop_left = int(np.floor(crop.x()))
        crop_top = int(np.floor(crop.y()))
        crop_right = crop_left + width
        crop_bottom = crop_top + height

        source_width = self.photo_view.pixmap.width()
        source_height = self.photo_view.pixmap.height()
        source_left = max(0, crop_left)
        source_top = max(0, crop_top)
        source_right = min(source_width, crop_right)
        source_bottom = min(source_height, crop_bottom)

        outside_mask = np.full((height, width), 255, dtype=np.uint8)
        if source_right > source_left and source_bottom > source_top:
            dest_left = source_left - crop_left
            dest_top = source_top - crop_top
            dest_right = dest_left + source_right - source_left
            dest_bottom = dest_top + source_bottom - source_top
            outside_mask[dest_top:dest_bottom, dest_left:dest_right] = 0

        white_image = QImage(
            width, height, QImage.Format.Format_RGBA8888
        )
        white_image.fill(QColor("#FFFFFF"))
        white_canvas_layer = PhotoLayer(
            white_image,
            name="Белый холст за пределами оригинала",
            layer_id="white-canvas",
            mask=self._grayscale_qimage_from_array(outside_mask),
            metadata={
                "role": "outside-original-white-canvas",
                "source_bounds": (
                    source_left, source_top, source_right, source_bottom
                ),
            },
        )

        crop_in_result = QRectF(
            crop.x() - crop_left,
            crop.y() - crop_top,
            crop.width(),
            crop.height()
        )
        rasterized_corner = QImage(
            width, height, QImage.Format.Format_RGBA8888
        )
        rasterized_corner.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rasterized_corner)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.photo_view.draw_format_corner(painter, crop_in_result)
        painter.end()

        rasterized_corner = rasterized_corner.convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        corner_rows = np.frombuffer(
            rasterized_corner.constBits(),
            dtype=np.uint8,
            count=rasterized_corner.sizeInBytes()
        ).reshape(height, rasterized_corner.bytesPerLine())
        corner_pixels = corner_rows[:, :width * 4].reshape(
            height, width, 4
        )
        corner_mask = corner_pixels[:, :, 3].copy()

        corner_image = QImage(
            width, height, QImage.Format.Format_RGBA8888
        )
        corner_image.fill(QColor("#FFFFFF"))
        corner_layer = PhotoLayer(
            corner_image,
            name="Защищённый белый уголок для штампа",
            layer_id="protected-stamp-corner",
            mask=self._grayscale_qimage_from_array(corner_mask),
            metadata={
                "role": "protected-stamp-corner",
                "enabled": bool(self.photo_view.corner_enabled),
                "width_mm": float(self.photo_view.corner_width_mm),
                "height_mm": float(self.photo_view.corner_height_mm),
                "format_width_mm": float(self.photo_view.format_width_mm),
                "format_height_mm": float(self.photo_view.format_height_mm),
                "corner_type": int(self.photo_view.corner_type),
                "corner_shape": str(self.photo_view.corner_shape),
                "position": (
                    "left-bottom"
                    if self.photo_view.corner_type == 4
                    else "right-bottom"
                ),
                "frame_rect": (
                    crop_in_result.x(),
                    crop_in_result.y(),
                    crop_in_result.width(),
                    crop_in_result.height(),
                ),
            },
        )

        self.background_protected_mask = corner_mask.copy()
        return [
            background_layer,
            PhotoLayer(
                working_pixmap.toImage(),
                name="Фотография",
                layer_id="working-image",
            ),
            white_canvas_layer,
            corner_layer,
        ]

    def lock_composition(self):
        # Сначала создаём копию по уже сформированной рамке.
        working_pixmap = self.create_working_copy(include_corner=False)

        if working_pixmap is None or working_pixmap.isNull():
            QMessageBox.warning(
                self,
                "Создание рабочей копии",
                "Не удалось создать рабочую копию фотографии."
            )
            return

        view = self.photo_view
        composition_rect = QRectF(view.crop_rect)
        self.photo_document = PhotoDocument(
            original_image=self.original_pixmap.toImage(),
            canvas_size=working_pixmap.size(),
            layers=self._make_protected_working_layers(
                working_pixmap,
                view.crop_rect,
            ),
            composition_rect=composition_rect,
            source_path=self.current_photo_path,
            # Сохраняем формат рамки в момент фиксации, а не выбранный позже.
            metadata={"export_format": self.format_combo.currentData()},
        )
        self._export_size = None
        self._export_cache = None
        working_pixmap = QPixmap.fromImage(
            self.composite_renderer.render(self.photo_document)
        )
        self.markup_state_before_lock = {
            "pixmap": view.pixmap.copy(),
            "crop_rect": QRectF(view.crop_rect),
            "left_eye": QPointF(view.left_eye) if view.left_eye is not None else None,
            "right_eye": QPointF(view.right_eye) if view.right_eye is not None else None,
            "head_top": QPointF(view.head_top) if view.head_top is not None else None,
            "chin": QPointF(view.chin) if view.chin is not None else None,
            "face_rect": QRectF(view.face_rect) if view.face_rect is not None else None,
            "rotation_angle": view.rotation_angle,
            "zoom": view.zoom,
            "fit_scale": view.fit_scale,
            "pan_offset": QPointF(view.pan_offset),
            "crop_ratio_locked": view.crop_ratio_locked,
            "margin_fit_applied": view.margin_fit_applied,
            "show_markup_points": view.show_markup_points,
            "active_tool": view.active_tool,
        }

        state = self.markup_state_before_lock
        self.markup_saved_zoom = view.zoom
        self.markup_saved_pan_x = view.pan_offset.x()
        self.markup_saved_pan_y = view.pan_offset.y()

        # Рабочая копия отдельна от исходника и становится предпросмотром.
        self.working_pixmap = working_pixmap.copy()
        self.current_pixmap = working_pixmap.copy()
        self.update_file_preview(self.working_pixmap)

        # Сохраняем экранный размер и положение зелёной рамки:
        # вырезанный кадр займёт её прежнее место без меток и линий.
        source_width = view.pixmap.width()
        source_height = view.pixmap.height()
        crop = state["crop_rect"]
        crop_left = int(np.floor(crop.x()))
        crop_top = int(np.floor(crop.y()))
        source_fit_scale = state["fit_scale"]
        if source_fit_scale is None or source_fit_scale <= 0:
            source_fit_scale = min(
                max(1, view.width()) / source_width,
                max(1, view.height()) / source_height
            )
        display_scale = source_fit_scale * state["zoom"]
        result_fit_scale = min(
            max(1, view.width()) / working_pixmap.width(),
            max(1, view.height()) / working_pixmap.height()
        )

        view.update_photo_pixels(working_pixmap.copy())
        view.fit_scale = None
        if result_fit_scale > 0:
            view.zoom = display_scale / result_fit_scale
            view.pan_offset = QPointF(
                state["pan_offset"].x()
                + display_scale * (
                    crop_left
                    - (source_width - working_pixmap.width()) / 2
                ),
                state["pan_offset"].y()
                + display_scale * (
                    crop_top
                    - (source_height - working_pixmap.height()) / 2
                )
            )
        self.background_source_pixmap = None
        self._set_background_mask_edit(None)
        view.background_mask = None
        view.composition_locked = True
        view.update()

        self.left_eye_button.setEnabled(False)
        self.right_eye_button.setEnabled(False)
        self.head_top_button.setEnabled(False)
        self.chin_button.setEnabled(False)
        self.align_eyes_button.setEnabled(False)
        self.align_vertical_button.setEnabled(False)
        self.reset_marks_button.setEnabled(False)
        self.detect_button.setEnabled(False)
        self.top_margin_input.setEnabled(False)
        self.bottom_margin_input.setEnabled(False)
        self.apply_margins_button.setEnabled(False)
        self.lock_composition_button.setEnabled(False)
        self.unlock_composition_button.setEnabled(True)

        self.change_mode("processing")
        self.statusBar().showMessage(
            "Рабочая копия создана. Исходная фотография не изменена."
        )

    def unlock_composition(self):
        format_changed = (
            self.photo_document is not None
            and self.photo_document.metadata.get("export_format")
            != self.format_combo.currentData()
        )
        state = self.markup_state_before_lock
        view = self.photo_view

        if state is not None:
            restored_pixmap = state["pixmap"].copy()
            view.update_photo_pixels(restored_pixmap)
            self.current_pixmap = restored_pixmap.copy()
            view.crop_rect = QRectF(state["crop_rect"])
            view.left_eye = QPointF(state["left_eye"]) if state["left_eye"] is not None else None
            view.right_eye = QPointF(state["right_eye"]) if state["right_eye"] is not None else None
            view.head_top = QPointF(state["head_top"]) if state["head_top"] is not None else None
            view.chin = QPointF(state["chin"]) if state["chin"] is not None else None
            view.face_rect = QRectF(state["face_rect"]) if state["face_rect"] is not None else None
            view.rotation_angle = state["rotation_angle"]
            view.zoom = state["zoom"]
            view.fit_scale = state["fit_scale"]
            view.pan_offset = QPointF(state["pan_offset"])
            view.crop_ratio_locked = state["crop_ratio_locked"]
            view.margin_fit_applied = state["margin_fit_applied"]
            view.show_markup_points = state["show_markup_points"]
            view.set_tool(state["active_tool"])
            self.update_file_preview(restored_pixmap)

        self.markup_state_before_lock = None
        self.photo_document = None
        self._export_size = None
        self._export_cache = None
        self.working_pixmap = None
        self.background_source_pixmap = None
        self._set_background_mask_edit(None)
        self.background_protected_mask = None
        view.background_mask = None
        view.composition_locked = False
        if format_changed:
            # Новый выбор не менял зафиксированный документ. Применяем его
            # к рамке только после возвращения в разметку.
            self.on_format_selected(self.format_combo.currentIndex())
        view.update()

        self.left_eye_button.setEnabled(True)
        self.right_eye_button.setEnabled(True)
        self.head_top_button.setEnabled(True)
        self.chin_button.setEnabled(True)
        self.align_eyes_button.setEnabled(True)
        self.align_vertical_button.setEnabled(True)
        self.reset_marks_button.setEnabled(True)
        self.detect_button.setEnabled(True)
        self.top_margin_input.setEnabled(True)
        self.bottom_margin_input.setEnabled(True)
        self.apply_margins_button.setEnabled(True)
        self.lock_composition_button.setEnabled(True)
        self.unlock_composition_button.setEnabled(False)

        self.change_mode("markup")
        self.statusBar().showMessage(
            "Предыдущая композиция восстановлена. Разметка снова доступна."
        )
    def reset_markup(self):
        self.photo_view.reset_marks()

        # После сброса разметки разрешаем свободное изменение рамки.
        self.photo_view.crop_ratio_locked = False
        self.photo_view.margin_fit_applied = False

        self.photo_view.create_initial_crop()
        self.photo_view.update()
        self.set_markup_tool("move")
        self.statusBar().showMessage(
            "Разметка сброшена. Рамка возвращена в исходное положение."
        )

    def open_photo(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выбрать фотографию",
            "",
            "Изображения (*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff)"
        )
        if path:
            self.load_photo(path)



    def copy_dropped_photos(self, paths):
        # Проверяем, выбрана ли текущая папка.
        if not self.current_folder or not os.path.isdir(self.current_folder):
            QMessageBox.warning(
                self,
                "Нет выбранной папки",
                "Сначала выберите папку для фотографий."
            )
            return

        copied_count = 0
        skipped_count = 0

        # Обрабатываем каждый перетащенный файл или папку.
        for source_path in paths:

            # Если перетащена папка — копируем её целиком.
            if os.path.isdir(source_path):
                folder_name = os.path.basename(
                    os.path.normpath(source_path)
                )

                destination_path = os.path.join(
                    self.current_folder,
                    folder_name
                )

                # Не копируем папку саму в себя.
                source_absolute = os.path.abspath(source_path)
                destination_absolute = os.path.abspath(destination_path)

                if (
                    source_absolute == destination_absolute
                    or destination_absolute.startswith(
                        source_absolute + os.sep
                    )
                ):
                    skipped_count += 1
                    continue

                # Если папка с таким названием уже существует,
                # предлагаем пользователю выбрать другое имя.
                if os.path.exists(destination_path):
                    new_name, accepted = QInputDialog.getText(
                        self,
                        "Папка уже существует",
                        f"Папка «{folder_name}» уже существует.\n"
                        "Введите новое название:"
                    )

                    if not accepted or not new_name.strip():
                        skipped_count += 1
                        continue

                    new_name = new_name.strip()

                    # Проверяем, что введено именно название папки.
                    if (
                        new_name in (".", "..")
                        or os.path.basename(new_name) != new_name
                        or any(char in new_name for char in '<>:"/\\|?*')
                    ):
                        QMessageBox.warning(
                            self,
                            "Неверное название",
                            "Введите корректное название папки."
                        )
                        skipped_count += 1
                        continue

                    destination_path = os.path.join(
                        self.current_folder,
                        new_name
                    )

                    # Не перезаписываем существующие файлы и папки.
                    if os.path.exists(destination_path):
                        QMessageBox.warning(
                            self,
                            "Папка уже существует",
                            "Папка с таким названием тоже уже существует."
                        )
                        skipped_count += 1
                        continue

                try:
                    # Копируем папку полностью, включая вложенные папки.
                    shutil.copytree(source_path, destination_path)
                    copied_count += 1
                except OSError as error:
                    QMessageBox.warning(
                        self,
                        "Ошибка копирования",
                        f"Не удалось скопировать папку:\n{error}"
                    )
                    skipped_count += 1

            # Если перетащен отдельный файл.
            elif os.path.isfile(source_path):

                # Принимаем только поддерживаемые изображения.
                if not source_path.lower().endswith(IMAGE_EXTENSIONS):
                    skipped_count += 1
                    continue

                destination_path = os.path.join(
                    self.current_folder,
                    os.path.basename(source_path)
                )

                # Не копируем файл сам в себя и не перезаписываем существующий.
                if (
                    os.path.abspath(source_path)
                    == os.path.abspath(destination_path)
                    or os.path.exists(destination_path)
                ):
                    skipped_count += 1
                    continue

                try:
                    shutil.copy2(source_path, destination_path)
                    copied_count += 1
                except OSError:
                    skipped_count += 1

        # Обновляем сетку фотографий.
        self.load_folder_images(self.current_folder)

        self.statusBar().showMessage(
            f"Скопировано: {copied_count}, пропущено: {skipped_count}"
        )



    # -----------------------------------------------------
    # ПЕРЕМЕЩЕНИЕ ФОТОГРАФИЙ В ДРУГУЮ ПАПКУ
    # -----------------------------------------------------

    def move_photos_to_folder(self, source_paths, destination_folder):
        moved_count = 0

        for source_path in source_paths:
            if not os.path.isfile(source_path):
                continue

            destination_path = os.path.join(
                destination_folder,
                os.path.basename(source_path)
            )

            # Не перемещаем файл в ту же папку.
            if os.path.abspath(source_path) == os.path.abspath(destination_path):
                continue

            # Не перезаписываем существующие файлы.
            if os.path.exists(destination_path):
                continue

            try:
                shutil.move(source_path, destination_path)
                moved_count += 1

                # Если переместили открытую фотографию,
                # обновляем путь к ней.
                if (
                    self.current_photo_path
                    and os.path.abspath(self.current_photo_path)
                    == os.path.abspath(source_path)
                ):
                    self.current_photo_path = destination_path

            except OSError:
                continue

        # Обновляем список фотографий текущей папки.
        if self.current_folder and os.path.isdir(self.current_folder):
            self.load_folder_images(self.current_folder)

        self.statusBar().showMessage(
            f"Перемещено фотографий: {moved_count}"
        )

    # -----------------------------------------------------
    # ЗАГРУЗКА МИНИАТЮР ТОЛЬКО ДЛЯ ВИДИМОЙ ЧАСТИ СЕТКИ
    # -----------------------------------------------------

    def load_visible_thumbnails(self):

        if not self.photo_files:
            return

        # Определяем верхний и нижний видимые элементы.
        top_item = self.files_list.itemAt(5, 5)

        bottom_item = self.files_list.itemAt(
            5,
            self.files_list.viewport().height() - 5
        )

        if top_item is None:
            first_index = 0
        else:
            first_index = self.files_list.row(top_item)

        if bottom_item is None:
            last_index = min(
                len(self.photo_files) - 1,
                first_index + 30
            )
        else:
            last_index = self.files_list.row(bottom_item)

        # Добавляем запас элементов вокруг видимой области.
        first_index = max(0, first_index - 10)

        last_index = min(
            len(self.photo_files) - 1,
            last_index + 20
        )

        visible_range = (first_index, last_index)

        if visible_range == self.last_visible_range:
            return

        self.last_visible_range = visible_range

        # Отправляем на загрузку только нужные миниатюры.
        for index in range(first_index, last_index + 1):

            path = self.photo_files[index]

            if path in self.loading_paths:
                continue

            item = self.items_by_path[path]

            if not item.icon().isNull():
                continue

            self.loading_paths.add(path)

            task = ThumbnailTask(
                path,
                self.thumbnail_signals
            )

            self.thread_pool.start(task)

    # -----------------------------------------------------
    # ЗАПУСК ЗАГРУЗКИ МИНИАТЮР ПРИ ПРОКРУТКЕ
    # -----------------------------------------------------

    def schedule_thumbnail_loading(self):

        # Сбрасываем предыдущий диапазон.
        self.last_visible_range = (-1, -1)

        # Даём сетке время завершить прокрутку.
        # Затем загружаем миниатюры новых видимых фотографий.
        QTimer.singleShot(
            50,
            self.load_visible_thumbnails
        )

    # -----------------------------------------------------
    # ПРИЁМ ГОТОВОЙ МИНИАТЮРЫ И ОТОБРАЖЕНИЕ В СЕТКЕ
    # -----------------------------------------------------

    def on_thumbnail_loaded(self, path, image):

        self.loading_paths.discard(path)

        item = self.items_by_path.get(path)

        if item is None:
            return

        if image is None or image.isNull():
            item.setText(
                os.path.basename(path) + "\nОшибка"
            )
            return

        pixmap = QPixmap.fromImage(image)

        item.setIcon(
            QIcon(pixmap)
        )

        item.setText(
            os.path.basename(path)
        )

    def on_folder_selected(self, index):
        folder = self.folder_model.filePath(index)
        if not os.path.isdir(folder):
            return
        self.current_folder = folder
        self.settings.setValue("last_folder", folder)        
        self.load_folder_images(folder)

    # -----------------------------------------------------
    # ЗАГРУЗКА СПИСКА ФОТОГРАФИЙ В ВЫБРАННОЙ ПАПКЕ
    # -----------------------------------------------------

    def load_folder_images(self, folder):

        # Очищаем старый список фотографий.
        self.files_list.clear()

        self.photo_files.clear()
        self.items_by_path.clear()
        self.loading_paths.clear()

        # Сбрасываем запомненный видимый диапазон.
        self.last_visible_range = (-1, -1)

        try:
            filenames = sorted(
                os.listdir(folder),
                key=str.lower
            )

        except OSError as error:
            QMessageBox.warning(
                self,
                "Ошибка",
                f"Не удалось прочитать папку:\n{error}"
            )
            return

        # Создаём элементы сетки без загрузки миниатюр.
        for filename in filenames:

            if not filename.lower().endswith(IMAGE_EXTENSIONS):
                continue

            path = os.path.join(folder, filename)

            if not os.path.isfile(path):
                continue

            self.photo_files.append(path)

            # Создаём пустой элемент сетки.
            item = QListWidgetItem(filename)

            # Сохраняем полный путь к фотографии.
            item.setData(
                Qt.ItemDataRole.UserRole,
                path
            )

            # Задаём размер ячейки.
            item.setSizeHint(
                QSize(ITEM_WIDTH, ITEM_HEIGHT)
            )

            self.files_list.addItem(item)

            # Запоминаем связь пути с элементом сетки.
            self.items_by_path[path] = item

        # Если фотографии найдены.
        if self.photo_files:

            # Выделяем первую фотографию.
            self.files_list.setCurrentRow(0)

            # Сохраняем прежнее поведение:
            # при выборе папки первая фотография открывается автоматически.
            self.load_photo(self.photo_files[0])

            self.statusBar().showMessage(
                f"Найдено фотографий: {len(self.photo_files)}"
            )

            # Через небольшую задержку загружаем видимые миниатюры.
            QTimer.singleShot(
                50,
                self.load_visible_thumbnails
            )

        else:
            self.file_preview.setText("Предпросмотр")

            self.statusBar().showMessage(
                "В выбранной папке нет фотографий"
            )

            
    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Выбрать папку с фотографиями"
        )
        if not folder:
            return
        self.current_folder = folder
        self.load_folder_images(folder)

    # -----------------------------------------------------
    # ВЫБОР ФОТОГРАФИИ В СЕТКЕ БЕЗ ЕЁ ОТКРЫТИЯ
    # -----------------------------------------------------

    # -----------------------------------------------------
    # ВЫБОР ФОТОГРАФИИ И ОБНОВЛЕНИЕ ПРЕДПРОСМОТРА
    # -----------------------------------------------------

    def on_current_item_changed(self, current, previous):

        if current is None:
            return

        path = current.data(Qt.ItemDataRole.UserRole)

        if not path or not os.path.isfile(path):
            return

        # Показываем выбранную фотографию в нижнем предпросмотре.
        preview_pixmap = QPixmap(path)

        if not preview_pixmap.isNull():
            self.update_file_preview(preview_pixmap)

        # Обновляем строку состояния.
        self.statusBar().showMessage(
            f"Выбрано: {os.path.basename(path)}"
        )

    # -----------------------------------------------------
    # ОТКРЫТИЕ ФОТОГРАФИИ ДВОЙНЫМ ЩЕЛЧКОМ
    # -----------------------------------------------------

    def open_selected_photo(self, item=None):

        # Если элемент передан двойным щелчком — используем его.
        # Иначе берём текущую выделенную фотографию.
        if item is None:
            item = self.files_list.currentItem()

        if item is None:
            return

        path = item.data(Qt.ItemDataRole.UserRole)

        if path and os.path.isfile(path):
            self.load_photo(path)

    def photo_grid_has_focus(self):
        """Проверяет, находится ли фокус на сетке фотографий или дереве папок."""
        focused_widget = QApplication.focusWidget()

        if focused_widget is None:
            return False

        return (
            focused_widget is self.files_list
            or self.files_list.isAncestorOf(focused_widget)
            or focused_widget is self.folder_tree
            or self.folder_tree.isAncestorOf(focused_widget)
        )

    # -----------------------------------------------------
    # КОНТЕКСТНОЕ МЕНЮ ФОТОГРАФИЙ
    # -----------------------------------------------------

    def show_photo_context_menu(self, position):

        # Проверяем, щёлкнули по фотографии или по пустому месту.
        item = self.files_list.itemAt(position)

        # Если фотография не была выделена, выделяем только её.
        if item is not None and not item.isSelected():
            self.files_list.clearSelection()
            self.files_list.setCurrentItem(item)
            item.setSelected(True)

        # Получаем список выделенных фотографий.
        selected_items = self.files_list.selectedItems()

        # Создаём контекстное меню.
        menu = QMenu(self)

        # Если щёлкнули по фотографии, показываем команды для неё.
        if item is not None and selected_items:

            menu.addAction(
                f"Выбрано фотографий: {len(selected_items)}"
            ).setEnabled(False)

            menu.addSeparator()

            # Открытие фотографии.
            open_action = menu.addAction("Открыть фотографию")
            open_action.triggered.connect(
                self.open_selected_photo
            )

            # Копирование.
            copy_action = menu.addAction("Копировать")
            copy_action.triggered.connect(
                self.copy_selected_photos
            )

            # Вырезание.
            cut_action = menu.addAction("Вырезать")
            cut_action.triggered.connect(
                self.cut_selected_photos
            )

            menu.addSeparator()

            # Удаление.
            delete_action = menu.addAction("Удалить")
            delete_action.triggered.connect(
                self.delete_selected_photos
            )

            # Переименование доступно только для одной фотографии.
            rename_action = menu.addAction("Переименовать")
            rename_action.setEnabled(
                len(selected_items) == 1
            )
            rename_action.triggered.connect(
                self.rename_selected_photo
            )

            menu.addSeparator()

        # Вставка доступна и при щелчке по пустому месту.
        paste_action = menu.addAction("Вставить")
        paste_action.setEnabled(
            bool(getattr(self, "copied_photo_paths", []))
        )
        paste_action.triggered.connect(
            self.paste_photos
        )

        menu.addSeparator()

        # Создание папки доступно в любом месте сетки.
        new_folder_action = menu.addAction("Создать папку")
        new_folder_action.triggered.connect(
            self.create_new_folder
        )

        # Показываем меню в месте щелчка.
        menu.exec(
            self.files_list.viewport().mapToGlobal(position)
        )

    # -----------------------------------------------------
    # ПОДГОТОВКА ФОТОГРАФИЙ К КОПИРОВАНИЮ
    # -----------------------------------------------------

    def copy_selected_photos(self):

        selected_items = self.files_list.selectedItems()

        if not selected_items:
            return

        self.copied_photo_paths = []

        # Запоминаем, что выбран режим копирования.
        self.cut_mode = False

        for item in selected_items:
            path = item.data(Qt.ItemDataRole.UserRole)

            if path and os.path.isfile(path):
                self.copied_photo_paths.append(path)

        self.statusBar().showMessage(
            f"Подготовлено к копированию: "
            f"{len(self.copied_photo_paths)} фотографий"
        )

    # -----------------------------------------------------
    # ПОДГОТОВКА ФОТОГРАФИЙ К ПЕРЕМЕЩЕНИЮ (ВЫРЕЗАНИЕ)
    # -----------------------------------------------------

    def cut_selected_photos(self):

        selected_items = self.files_list.selectedItems()

        if not selected_items:
            return

        self.copied_photo_paths = []

        # Запоминаем, что выбран режим перемещения.
        self.cut_mode = True

        for item in selected_items:
            path = item.data(Qt.ItemDataRole.UserRole)

            if path and os.path.isfile(path):
                self.copied_photo_paths.append(path)

        self.statusBar().showMessage(
            f"Подготовлено к перемещению: "
            f"{len(self.copied_photo_paths)} фотографий"
        )

    # -----------------------------------------------------
    # УДАЛЕНИЕ ВЫБРАННЫХ ФОТОГРАФИЙ
    # -----------------------------------------------------

    def delete_selected_photos(self):

        selected_items = self.files_list.selectedItems()

        if not selected_items:
            return

        # Получаем пути выбранных фотографий.
        paths = []

        for item in selected_items:
            path = item.data(Qt.ItemDataRole.UserRole)

            if path and os.path.isfile(path):
                paths.append(path)

        if not paths:
            return

        # Обязательно спрашиваем подтверждение.
        answer = QMessageBox.question(
            self,
            "Удаление фотографий",
            f"Переместить выбранные фотографии в Корзину ({len(paths)} шт.)?",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        # Структура для вызова системной функции Windows.
        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", wintypes.WORD),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", wintypes.LPVOID),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        deleted_count = 0
        failed_paths = []

        for path in paths:
            try:
                # Подготавливаем путь для Windows.
                source_buffer = ctypes.create_unicode_buffer(
                    path + "\0\0"
                )

                operation = SHFILEOPSTRUCTW()
                operation.hwnd = None

                # FO_DELETE — операция удаления.
                operation.wFunc = 3

                operation.pFrom = ctypes.cast(
                    source_buffer,
                    wintypes.LPCWSTR
                )

                operation.pTo = None

                # Разрешаем перемещение в Корзину.
                # Отключаем лишние системные запросы.
                operation.fFlags = (
                    0x0040 |  # FOF_ALLOWUNDO
                    0x0010 |  # FOF_NOCONFIRMATION
                    0x0004 |  # FOF_SILENT
                    0x0400    # FOF_NOERRORUI
                )

                operation.fAnyOperationsAborted = False
                operation.hNameMappings = None
                operation.lpszProgressTitle = None

                # Вызываем системную функцию Windows.
                result = ctypes.windll.shell32.SHFileOperationW(
                    ctypes.byref(operation)
                )

                if result == 0 and not operation.fAnyOperationsAborted:
                    deleted_count += 1

                    # Если удалили открытую фотографию, очищаем редактор.
                    if (
                        self.current_photo_path
                        and os.path.abspath(self.current_photo_path)
                        == os.path.abspath(path)
                    ):
                        self.current_photo_path = None
                        self.current_pixmap = QPixmap()
                        self.photo_view.set_photo(QPixmap())
                        self.file_preview.setText("Предпросмотр")

                else:
                    failed_paths.append(
                        f"{os.path.basename(path)} (код {result})"
                    )

            except Exception as error:
                failed_paths.append(
                    f"{os.path.basename(path)} ({error})"
                )

        # Обновляем список фотографий.
        if self.current_folder:
            self.load_folder_images(self.current_folder)

        self.statusBar().showMessage(
            f"Перемещено в Корзину: {deleted_count}"
        )

        # Показываем файлы, которые не удалось переместить.
        if failed_paths:
            QMessageBox.warning(
                self,
                "Не удалось переместить в Корзину",
                "Не удалось обработать следующие файлы:\n\n"
                + "\n".join(failed_paths)
            )

    # -----------------------------------------------------
    # ПЕРЕИМЕНОВАНИЕ ВЫБРАННОЙ ФОТОГРАФИИ
    # -----------------------------------------------------

    def rename_selected_photo(self):

        selected_items = self.files_list.selectedItems()

        # Переименовывать можно только одну фотографию.
        if len(selected_items) != 1:
            return

        selected_item = selected_items[0]

        old_path = selected_item.data(
            Qt.ItemDataRole.UserRole
        )

        if not old_path or not os.path.isfile(old_path):
            return

        old_name = os.path.basename(old_path)

        new_name, accepted = QInputDialog.getText(
            self,
            "Переименование фотографии",
            "Новое имя файла:",
            text=old_name
        )

        if not accepted or not new_name.strip():
            return

        new_name = new_name.strip()

        # Проверяем запрещённые Windows символы.
        invalid_chars = '<>:"/\\|?*'

        if any(char in new_name for char in invalid_chars):
            QMessageBox.warning(
                self,
                "Недопустимое название",
                "Название содержит запрещённые символы."
            )
            return

        new_path = os.path.join(
            os.path.dirname(old_path),
            new_name
        )

        if os.path.exists(new_path):
            QMessageBox.warning(
                self,
                "Файл существует",
                "Файл с таким названием уже существует."
            )
            return

        try:
            os.rename(old_path, new_path)

            # Если переименовали открытую фотографию,
            # обновляем путь к ней.
            if (
                self.current_photo_path
                and os.path.abspath(self.current_photo_path)
                == os.path.abspath(old_path)
            ):
                self.current_photo_path = new_path

            # Обновляем список фотографий.
            self.load_folder_images(self.current_folder)

            self.statusBar().showMessage(
                f"Переименовано: {new_name}"
            )

        except OSError as error:
            QMessageBox.warning(
                self,
                "Ошибка переименования",
                str(error)
            )

    def create_new_folder(self):
        from PySide6.QtWidgets import QInputDialog

        # Запрашиваем название новой папки.
        folder_name, accepted = QInputDialog.getText(
            self,
            "Новая папка",
            "Введите название папки:"
        )

        if not accepted or not folder_name.strip():
            return

        folder_name = folder_name.strip()

        # Запрещаем недопустимые символы Windows.
        invalid_chars = '<>:"/\\|?*'

        if any(char in folder_name for char in invalid_chars):
            QMessageBox.warning(
                self,
                "Недопустимое название",
                "Название содержит запрещённые символы."
            )
            return

        # Формируем полный путь новой папки.
        new_path = os.path.join(
            self.current_folder,
            folder_name
        )

        # Проверяем, нет ли папки с таким названием.
        if os.path.exists(new_path):
            QMessageBox.warning(
                self,
                "Папка существует",
                "Папка с таким названием уже существует."
            )
            return

        try:
            # Создаём папку.
            os.makedirs(new_path)

            # Обновляем список фотографий.
            self.load_folder_images(self.current_folder)

            self.statusBar().showMessage(
                f"Создана папка: {folder_name}"
            )

        except OSError as error:
            QMessageBox.warning(
                self,
                "Ошибка",
                f"Не удалось создать папку:\n{error}"
            )

    def paste_photos(self):
        # Получаем список фотографий из буфера обмена.
        paths = getattr(self, "copied_photo_paths", [])

        if not paths:
            return

        copied_count = 0
        cut_mode = getattr(self, "cut_mode", False)

        for source_path in paths:
            if not os.path.isfile(source_path):
                continue

            destination_path = os.path.join(
                self.current_folder,
                os.path.basename(source_path)
            )

            # Не копируем файл сам в себя.
            if os.path.abspath(source_path) == os.path.abspath(destination_path):
                continue

            # Проверяем, существует ли файл с таким именем.
            if os.path.exists(destination_path):

                answer = QMessageBox.question(
                    self,
                    "Файл уже существует",
                    f"Файл с именем «{os.path.basename(source_path)}» "
                    "уже существует в этой папке.\n\nЗаменить его?",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.No
                )

                # Отмена — прекращаем вставку.
                if answer == QMessageBox.StandardButton.Cancel:
                    break

                # Нет — пропускаем этот файл.
                if answer == QMessageBox.StandardButton.No:
                    continue

                # Да — заменяем существующий файл.
                try:
                    shutil.copy2(source_path, destination_path)

                    # При вырезании удаляем исходник только после успешного копирования.
                    if cut_mode:
                        os.remove(source_path)

                    copied_count += 1

                except OSError as error:
                    QMessageBox.warning(
                        self,
                        "Ошибка вставки",
                        f"Не удалось заменить файл:\n{error}"
                    )

                continue

            # Если файла нет, выполняем обычную вставку.
            try:
                if cut_mode:
                    shutil.move(source_path, destination_path)
                else:
                    shutil.copy2(source_path, destination_path)

                copied_count += 1

            except OSError as error:
                QMessageBox.warning(
                    self,
                    "Ошибка вставки",
                    f"Не удалось вставить файл:\n{error}"
                )

        # После вырезания оставляем в буфере только ещё не перемещённые файлы.
        if cut_mode:
            self.copied_photo_paths = [
                path for path in paths
                if os.path.isfile(path)
            ]

            if not self.copied_photo_paths:
                self.cut_mode = False

        self.statusBar().showMessage(
            f"Обработано фотографий: {copied_count}"
        )

        # Обновляем список фотографий после вставки.
        self.load_folder_images(self.current_folder)

    def load_photo(self, path):
        pixmap = QPixmap(path)
        if pixmap.isNull():
            QMessageBox.warning(
                self,
                "Ошибка",
                "Не удалось открыть выбранную фотографию."
            )
            return
        format_changed = (
            self.photo_document is not None
            and self.photo_document.metadata.get("export_format")
            != self.format_combo.currentData()
        )
        self.photo_document = None
        self._export_size = None
        self._export_cache = None
        self.current_photo_path = path
        self.current_pixmap = pixmap
        # Для новой фотографии рабочая копия ещё не создана.
        self.working_pixmap = None
        self.markup_state_before_lock = None

        # Сбрасываем сохранённое положение предыдущей фотографии.
        self.markup_saved_zoom = 1.0
        self.markup_saved_pan_x = 0.0
        self.markup_saved_pan_y = 0.0

        # Сохраняем нетронутую копию исходной фотографии.
        # Она понадобится для отмены обработки и восстановления оригинала.
        self.original_pixmap = pixmap.copy()

        # Чистая копия подготовленной фотографии для повторной обработки фона.
        self.background_source_pixmap = None

        # Сбрасываем маску удаления фона и кисть предыдущей фотографии.
        self._set_background_mask_edit(None)
        self.background_protected_mask = None
        self.photo_view.background_mask = None
        self.photo_view.background_brush_enabled = False
        self.photo_view.background_brush_dragging = False
        self.photo_view.background_brush_last_pos = None

        # При открытии новой фотографии сбрасываем выбранный цвет фона.
        self.background_color = None

        # Цвет, которым будет заменяться удалённый фон.
        self.background_replacement_color = QColor("#FFFFFF")
        self._update_background_replacement_color_controls()

        self.photo_view.set_photo(pixmap)
        if format_changed:
            self.on_format_selected(self.format_combo.currentIndex())

        # Снимаем блокировки кнопок, оставшиеся от предыдущей фотографии.
        for button in (
            self.detect_button,
            self.left_eye_button,
            self.right_eye_button,
            self.head_top_button,
            self.chin_button,
            self.align_eyes_button,
            self.align_vertical_button,
            self.reset_marks_button,
            self.apply_margins_button,
        ):
            button.setEnabled(True)

        is_gost = (
            self.current_markup_type == "Разметка по трём точкам (ГОСТ)"
        )
        self.top_margin_input.setEnabled(True)
        self.bottom_margin_input.setEnabled(not is_gost)
        self.head_size_input.setEnabled(not is_gost)
        self.face_size_input.setEnabled(is_gost)
        self.margin_fit_status.setText("Поля ещё не применены")
        self.set_markup_tool("move")

        # Для новой фотографии разрешаем разметку.
        self.photo_view.composition_locked = False
        self.photo_view.show_markup_points = True
        
        # Восстанавливаем кнопки разметки для новой фотографии.
        self.lock_composition_button.setEnabled(True)
        self.unlock_composition_button.setEnabled(False)

        # Возвращаем интерфейс в режим разметки.
        self.change_mode("markup")
        
        self.update_file_preview(pixmap)
        self.statusBar().showMessage(
            f"Открыто: {os.path.basename(path)} "
            f"({pixmap.width()} × {pixmap.height()} пикселей)"
        )

    def update_file_preview(self, pixmap):
        preview = pixmap.scaled(
            self.file_preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.file_preview.setPixmap(preview)

    def resizeEvent(self, event):
        if not self.current_pixmap.isNull():
            self.update_file_preview(self.current_pixmap)
        super().resizeEvent(event)

    def refresh_format_list(self):
        if not hasattr(self, "format_combo"):
            return

        search_text = (
            self.format_search.text().strip().lower()
            if hasattr(self, "format_search")
            else ""
        )

        previous_item = self.format_combo.currentData()
        previous_identity = self.get_format_identity(previous_item)

        self.format_combo.blockSignals(True)
        self.format_combo.clear()

        visible_formats = [
            item for item in self.formats
            if search_text == ""
            or search_text in str(item.get("name", "")).lower()
        ]

        formats_by_id = {
            self.get_format_identity(item): item
            for item in visible_formats
            if self.get_format_identity(item)
        }

        def add_section(title, items):
            if not items:
                return

            self.format_combo.addItem(title)
            header_index = self.format_combo.count() - 1
            header_item = self.format_combo.model().item(header_index)

            if header_item is not None:
                header_item.setEnabled(False)

            for item in items:
                name = str(item.get("name", "Без названия"))
                self.format_combo.addItem(name, item)

        added_ids = set()

        favorites = []
        for identity in self.favorite_format_ids:
            item = formats_by_id.get(identity)
            if item is not None and identity not in added_ids:
                favorites.append(item)
                added_ids.add(identity)

        recent = []
        for identity in self.recent_format_ids[:8]:
            item = formats_by_id.get(identity)
            if item is not None and identity not in added_ids:
                recent.append(item)
                added_ids.add(identity)

        all_other = [
            item for item in visible_formats
            if self.get_format_identity(item) not in added_ids
        ]

        add_section("★ ИЗБРАННОЕ", favorites)
        add_section("↻ ПОСЛЕДНИЕ 8", recent)
        add_section("ВСЕ ФОРМАТЫ", all_other)

        selected_index = -1

        if previous_identity:
            for i in range(self.format_combo.count()):
                item = self.format_combo.itemData(i)
                if self.get_format_identity(item) == previous_identity:
                    selected_index = i
                    break

        if selected_index < 0:
            for i in range(self.format_combo.count()):
                if isinstance(self.format_combo.itemData(i), dict):
                    selected_index = i
                    break

        self.format_combo.setCurrentIndex(selected_index)

        # Заполняем постоянно видимый список четырьмя форматами
        self.format_recent_list.clear()

        visible_items = []

        for i in range(self.format_combo.count()):
            item = self.format_combo.itemData(i)

            if isinstance(item, dict):
                visible_items.append(item)

            if len(visible_items) == 4:
                break

        for item in visible_items:
            identity = self.get_format_identity(item)
            is_favorite = identity in self.favorite_format_ids

            prefix = "★ " if is_favorite else ""

            list_item = QListWidgetItem(
                prefix + str(item.get("name", "Без названия"))
            )
            list_item.setData(
                Qt.ItemDataRole.UserRole,
                item
            )
            self.format_recent_list.addItem(list_item)

        self.format_combo.blockSignals(False)

        self.on_format_selected(selected_index)
        self.update_favorite_button()

    def recalculate_markup_bottom_margin(self, _value=None):
        if getattr(self, "_loading_markup_format", False):
            return
        if getattr(self, "current_markup_type", "") != "Разметка по двум точкам":
            return

        view = self.photo_view

        if view is None or view.composition_locked:
            return

        height = view.format_height_mm
        top = self.top_margin_input.value()
        head = self.head_size_input.value()

        new_bottom = max(0, height - top - head)

        self.bottom_margin_input.blockSignals(True)
        self.bottom_margin_input.setValue(new_bottom)
        self.bottom_margin_input.blockSignals(False)

    def recalculate_markup_head_size(self, _value=None):
        if getattr(self, "_loading_markup_format", False):
            return
        if getattr(self, "current_markup_type", "") != "Разметка по двум точкам":
            return

        view = self.photo_view

        if view is None or view.composition_locked:
            return

        height = view.format_height_mm
        top = self.top_margin_input.value()
        bottom = self.bottom_margin_input.value()

        new_head = max(0, height - top - bottom)

        self.head_size_input.blockSignals(True)
        self.head_size_input.setValue(new_head)
        self.head_size_input.blockSignals(False)
        
    def on_format_selected(self, index):
        if index < 0 or index >= self.format_combo.count():
            self.format_info.setText("Формат не выбран")
            return

        data = self.format_combo.itemData(index)
        if not isinstance(data, dict):
            self.format_info.setText("Нет данных о формате")
            return

        identity = self.get_format_identity(data)

        if identity:
            self.update_favorite_button()

            if (
                not self.recent_format_ids
                or self.recent_format_ids[0] != identity
            ):
                if identity in self.recent_format_ids:
                    self.recent_format_ids.remove(identity)

                self.recent_format_ids.insert(0, identity)
                self.recent_format_ids = self.recent_format_ids[:8]

                self.save_format_preferences()
                self.refresh_format_list()
                return

        # Синхронизируем выделение верхнего списка
        self.format_recent_list.clearSelection()

        for row in range(self.format_recent_list.count()):
            list_item = self.format_recent_list.item(row)
            list_data = list_item.data(Qt.ItemDataRole.UserRole)

            if self.get_format_identity(list_data) == identity:
                self.format_recent_list.setCurrentRow(row)
                break


        view = self.photo_view

        # Получаем параметры уголка из обычных полей или исходных XML-данных.
        xml_data = data.get("xml_data", {})

        if not isinstance(xml_data, dict):
            xml_data = {}

        corner_width = data.get("corner_width")
        if corner_width is None:
            corner_width = xml_data.get("CornerW", 0)

        corner_height = data.get("corner_height")
        if corner_height is None:
            corner_height = xml_data.get("CornerH", 0)

        corner_type = data.get("corner_type")
        if corner_type is None:
            corner_type = xml_data.get("CornerType", 0)

        try:
            corner_width = float(corner_width or 0)
            corner_height = float(corner_height or 0)
            corner_type = int(corner_type or 0)
        except (ValueError, TypeError):
            corner_width = 0
            corner_height = 0
            corner_type = 0

        # Если отдельный признак включения отсутствует,
        # определяем его по размерам и типу уголка.
        corner_enabled = data.get("corner_enabled")

        if corner_enabled is None:
            corner_enabled = (
                corner_width > 0
                and corner_height > 0
                and corner_type in (4, 5)
            )

        # Передаём настройки уголка в рабочую область.
        view.set_corner(
            enabled=corner_enabled,
            width_mm=corner_width,
            height_mm=corner_height,
            corner_type=corner_type,
            corner_shape=data.get(
                "corner_shape",
                "Закруглённый срез"
            )
        )

        width = data.get("width", 0)
        height = data.get("height", 0)
        note = data.get("note", "")

        units = data.get("units", "mm")
        unit_labels = {
            "mm": "мм",
            "in": "″",
            "px": "px"
        }
        unit_label = unit_labels.get(units, "ед.")

        self.current_format_units = units
        self.top_margin_label.setText(f"Верхнее поле, {unit_label}:")
        self.bottom_margin_label.setText(f"Нижнее поле, {unit_label}:")

        try:
            top_margin = float(data.get("top_margin", 3.0))
        except (TypeError, ValueError):
            top_margin = 3.0

        try:
            bottom_margin = float(data.get("bottom_margin", 3.0))
        except (TypeError, ValueError):
            bottom_margin = 3.0

        self._loading_markup_format = True
        self.top_margin_input.setValue(top_margin)
        self.bottom_margin_input.setValue(bottom_margin)

        self.current_markup_type = data.get(
            "markup_type",
            "Разметка по двум точкам"
        )

        try:
            self.current_face_size = float(
                data.get("face_size", data.get("front_size", 0)) or 0
            )
        except (TypeError, ValueError):
            self.current_face_size = 0

        is_gost = (
            self.current_markup_type == "Разметка по трём точкам (ГОСТ)"
        )

        head_value = data.get("head_size")

        if head_value is None or head_value == "":
            head_value = height - top_margin - bottom_margin

        try:
            head_size = float(head_value)
        except (TypeError, ValueError):
            head_size = 0

        self.head_size_input.setValue(max(0, head_size))
        self.face_size_input.setValue(self.current_face_size)

        self.bottom_margin_input.setEnabled(
            not is_gost and not view.composition_locked
        )
        self.head_size_input.setEnabled(
            not is_gost and not view.composition_locked
        )
        self.face_size_input.setEnabled(
            is_gost and not view.composition_locked
        )        

        if width and height:
            info = f"Размер: {width} × {height} {unit_label}"
            if not view.composition_locked:
                view.set_aspect_ratio(float(width), float(height))
        else:
            info = "Размер не указан"
            if not view.composition_locked:
                view.set_aspect_ratio(35, 45)

        if note:
            info += f"\nПримечание: {note}"

        self.format_info.setText(info)

        # При выборе формата не перемещаем рамку по меткам лица.
        # Поля по лицу пользователь применяет отдельной кнопкой.
        self._loading_markup_format = False            

    def get_format_identity(self, item):
        if not isinstance(item, dict):
            return None

        if item.get("_is_base") and item.get("source_id"):
            return "base:" + str(item["source_id"])

        favorite_id = item.get("_favorite_id")
        if favorite_id:
            return "custom:" + str(favorite_id)

        return None

    def save_format_preferences(self):
        self.settings.setValue(
            "recent_format_ids",
            self.recent_format_ids[:8]
        )
        self.settings.setValue(
            "favorite_format_ids",
            self.favorite_format_ids
        )
        self.settings.sync()

    def toggle_favorite_format(self):
        index = self.format_combo.currentIndex()
        if index < 0:
            return

        item = self.format_combo.itemData(index)
        identity = self.get_format_identity(item)
        if not identity:
            return

        if identity in self.favorite_format_ids:
            self.favorite_format_ids.remove(identity)
        else:
            self.favorite_format_ids.append(identity)

        self.save_format_preferences()
        self.refresh_format_list()

    def update_favorite_button(self):
        index = self.format_combo.currentIndex()
        item = self.format_combo.itemData(index) if index >= 0 else None
        identity = self.get_format_identity(item)

        is_favorite = (
            identity is not None
            and identity in self.favorite_format_ids
        )

        self.favorite_format_button.setText(
            "★" if is_favorite else "☆"
        )
        self.favorite_format_button.setToolTip(
            "Убрать из избранного"
            if is_favorite
            else "Добавить в избранное"
        )

    def on_format_preview_clicked(self, list_item):
        item = list_item.data(Qt.ItemDataRole.UserRole)

        if not isinstance(item, dict):
            return

        identity = self.get_format_identity(item)

        if not identity:
            return

        for i in range(self.format_combo.count()):
            combo_item = self.format_combo.itemData(i)

            if self.get_format_identity(combo_item) == identity:
                self.format_combo.setCurrentIndex(i)
                return
            
    def selected_format_index(self):
        name = self.format_combo.currentText()
        for index, item in enumerate(self.formats):
            if item.get("name") == name:
                return index
        return -1

    def add_format(self):
        dialog = FormatDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data = dialog.get_data()
        if not data["name"]:
            QMessageBox.warning(
                self,
                "Нет названия",
                "Введи название формата."
            )
            return
        if any(
            item.get("name", "").lower() == data["name"].lower()
            for item in self.formats
        ):
            QMessageBox.warning(
                self,
                "Такой формат уже есть",
                "Используй другое название."
            )
            return
        data["_favorite_id"] = str(uuid.uuid4())
        self.formats.append(data)
        self.save_formats()
        self.format_search.clear()
        self.refresh_format_list()
        index = self.format_combo.findText(data["name"])
        if index >= 0:
            self.format_combo.setCurrentIndex(index)


    def edit_format(self):
        index = self.selected_format_index()
        if index < 0:
            QMessageBox.information(
                self,
                "Формат не выбран",
                "Сначала выбери формат."
            )
            return

        old_item = self.formats[index]

        dialog = FormatDialog(self, old_item)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        data = dialog.get_data()

        if not data["name"]:
            QMessageBox.warning(
                self,
                "Нет названия",
                "Введи название формата."
            )
            return

        is_base = old_item.get("_is_base", False)

        if is_base:
            if not data["name"].endswith("(копия)"):
                data["name"] = data["name"] + " (копия)"

            source_id = old_item.get("source_id")

            data.pop("_is_base", None)
            data.pop("source_id", None)
            data.pop("_original_base", None)

            data["source_base_id"] = source_id
            data["source_base_name"] = old_item.get("name", "")

            data["_favorite_id"] = str(uuid.uuid4())

        else:
            data["_favorite_id"] = old_item.get(
                "_favorite_id",
                str(uuid.uuid4())
            )

        for other_index, item in enumerate(self.formats):
            if (
                other_index != index
                and item.get("name", "").lower() == data["name"].lower()
            ):
                QMessageBox.warning(
                    self,
                    "Такой формат уже есть",
                    "Используй другое название."
                )
                return

        if is_base:
            self.formats.append(data)
        else:
            self.formats[index] = data

        self.save_formats()
        self.refresh_format_list()

        selected = self.format_combo.findText(data["name"])
        if selected >= 0:
            self.format_combo.setCurrentIndex(selected)

    def delete_format(self):
        index = self.selected_format_index()
        if index < 0:
            return

        item = self.formats[index]
        name = item.get("name", "Без названия")
        identity = self.get_format_identity(item)

        answer = QMessageBox.question(
            self,
            "Удалить формат?",
            f"Удалить формат «{name}»?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        if item.get("_is_base"):
            source_id = item.get("source_id")
            if source_id and source_id not in self._deleted_base_ids:
                self._deleted_base_ids.append(source_id)

        self.formats.pop(index)

        if identity:
            if identity in self.favorite_format_ids:
                self.favorite_format_ids.remove(identity)

            if identity in self.recent_format_ids:
                self.recent_format_ids.remove(identity)

        self.save_format_preferences()
        self.save_formats()
        self.refresh_format_list()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PhotoStudio()
    window.show()
    sys.exit(app.exec())
