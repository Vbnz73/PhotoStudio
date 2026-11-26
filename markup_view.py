import math

from PySide6.QtWidgets import QWidget

from PySide6.QtGui import (
    QPixmap,
    QImage,
    QPainter,
    QPen,
    QColor,
    QPainterPath,
    QTransform,
)

from PySide6.QtCore import (
    Qt,
    QPointF,
    QRectF,
    QSize,
)

class MarkupView(QWidget):
    """Предпросмотр фотографии с рамкой кадрирования и метками глаз."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.pixmap = QPixmap()
        self.rotation_angle = 0.0        
        self.markup_enabled = False
        self.composition_locked = False
        self.active_tool = "move"
        self.crop_rect = QRectF()
        self.image_rect = QRectF()
        self.format_width_mm = 35.0
        
        # Настройки уголка выбранного формата.
        self.corner_enabled = False
        self.corner_width_mm = 0.0
        self.corner_height_mm = 0.0
        self.corner_type = 4
        self.corner_shape = "Закруглённый срез"        
        
        self.margin_fit_applied = False        
        self.left_eye = None
        self.right_eye = None
        self.head_top = None
        self.chin = None
        self.show_markup_points = True
        self.face_rect = None
        self.dragging = False
        self.drag_offset = QPointF()
        # Изменение размера рамки кадрирования мышью.
        self.frame_resize_handle = None
        self.frame_resize_start_rect = QRectF()
        self.frame_resize_start_pos = QPointF()
        self.frame_resize_mode = None

        # После применения формата рамка сохраняет его пропорции.
        self.crop_ratio_locked = False
        self.zoom = 1.0
        # Базовый масштаб фотографии для вписывания в рабочую область.
        self.fit_scale = None
        self.pan_offset = QPointF(0, 0)
        self.pan_start = QPointF()
        self.pan_dragging = False

        # Настройка меток глаз и увеличения.
        self.dragging_eye = None
        self.hover_eye = None
        self.magnifier_zoom = 3
        self.magnifier_size = 300
        self.magnifier_position = QPointF(0, 15)
        self.dragging_magnifier = False
        self.magnifier_drag_offset = QPointF()
        self.double_click_callback = None

        # Ручная коррекция маски удаления фона.
        self.background_brush_enabled = False

        # Тип кисти: paint — сохранить объект, erase — удалить объект.
        self.background_brush_type = "paint"

        # Размер и мягкость кисти.
        self.background_brush_size = 30
        self.background_brush_softness = 50

        # Общая маска удаления фона.
        self.background_mask = None

        # Состояние рисования маски мышью.
        self.background_brush_dragging = False
        self.background_brush_last_pos = None

        # Связь с обработчиком маски в главном окне.
        self.background_brush_callback = None

    def set_corner(
        self,
        enabled,
        width_mm,
        height_mm,
        corner_type,
        corner_shape
    ):
        """Получает настройки уголка из выбранного формата."""

        self.corner_enabled = bool(enabled)
        self.corner_width_mm = float(width_mm or 0)
        self.corner_height_mm = float(height_mm or 0)

        if corner_type in (4, 5):
            self.corner_type = corner_type

        self.corner_shape = (
            corner_shape or "Закруглённый срез"
        )

        self.update()


    def set_photo(self, pixmap):
        self.pixmap = pixmap

        # Новая фотография начинает независимую сессию разметки.
        # Настройки выбранного формата (пропорции и уголок) сохраняются.
        self.rotation_angle = 0.0
        self.composition_locked = False
        self.active_tool = "move"
        self.show_markup_points = True
        self.markup_enabled = False
        self.dragging = False
        self.drag_offset = QPointF()
        self.frame_resize_handle = None
        self.frame_resize_start_rect = QRectF()
        self.frame_resize_start_pos = QPointF()
        self.frame_resize_mode = None
        self.pan_dragging = False
        self.pan_start = QPointF()
        self.dragging_eye = None
        self.hover_eye = None
        self.dragging_magnifier = False
        self.magnifier_drag_offset = QPointF()
        self.background_brush_enabled = False
        self.background_brush_dragging = False
        self.background_brush_last_pos = None
        self.background_mask = None

        # Сбрасываем метки предыдущей фотографии.
        self.left_eye = None
        self.right_eye = None
        self.head_top = None
        self.chin = None
        self.face_rect = None

        # Для новой фотографии снимаем блокировку пропорций рамки.
        self.crop_ratio_locked = False
        self.margin_fit_applied = False

        # Возвращаем обычный масштаб и центрирование.
        # Сама фотография будет вписана в рабочую область в paintEvent().

        # Для каждой новой фотографии заново рассчитываем вписывание.
        self.fit_scale = None

        self.zoom = 1.0
        self.pan_offset = QPointF(0, 0)

        self.create_initial_crop()
        self.update()

    def update_photo_pixels(self, pixmap):
        # Обновляем только пиксели фотографии.
        # Кадрирование, отметки лица, масштаб и положение
        # фотографии оставляем без изменений.
        self.pixmap = pixmap

        # Перерисовываем рабочую область.
        self.update()

    def rotate_photo(self, angle):
        if self.pixmap.isNull():
            return
        self.rotation_angle += angle
        transform = QTransform()
        transform.rotate(self.rotation_angle)
        self.pixmap = self.pixmap.transformed(
            transform,
            Qt.TransformationMode.SmoothTransformation
        )
        self.left_eye = None
        self.right_eye = None
        self.face_rect = None
        self.create_initial_crop()
        self.update()

    def clamp_crop_inside_image(self):
        if self.pixmap.isNull() or self.crop_rect.isEmpty():
            return
        bounds = QRectF(0, 0, self.pixmap.width(), self.pixmap.height())
        crop = QRectF(self.crop_rect)
        if crop.width() <= 0 or crop.height() <= 0:
            self.create_initial_crop()
            return
        if crop.width() > bounds.width() or crop.height() > bounds.height():
            scale = min(bounds.width() / crop.width(), bounds.height() / crop.height())
            crop.setWidth(crop.width() * scale)
            crop.setHeight(crop.height() * scale)
        max_x = max(0.0, bounds.width() - crop.width())
        max_y = max(0.0, bounds.height() - crop.height())
        x = max(0.0, min(crop.x(), max_x))
        y = max(0.0, min(crop.y(), max_y))
        crop.moveTo(x, y)
        self.crop_rect = crop

    def align_eyes(self):
        """Выравнивает фотографию по центрам зрачков."""
        if (
            self.pixmap.isNull()
            or self.left_eye is None
            or self.right_eye is None
        ):
            return False

        dx = self.right_eye.x() - self.left_eye.x()
        dy = self.right_eye.y() - self.left_eye.y()

        if math.hypot(dx, dy) < 1:
            return False

        angle = math.degrees(math.atan2(dy, dx))
        if abs(angle) < 0.05:
            return True

        old_crop = QRectF(self.crop_rect)

        transform = QTransform()
        transform.rotate(-angle)

        true_matrix = QImage.trueMatrix(
            transform,
            self.pixmap.width(),
            self.pixmap.height()
        )

        transformed_pixmap = self.pixmap.transformed(
            transform,
            Qt.TransformationMode.SmoothTransformation
        )
        rotated_pixmap = QPixmap(transformed_pixmap.size())
        rotated_pixmap.fill(QColor("white"))
        painter = QPainter(rotated_pixmap)
        painter.drawPixmap(QPointF(0, 0), transformed_pixmap)
        painter.end()

        if self.left_eye is not None:
            self.left_eye = true_matrix.map(self.left_eye)
        if self.right_eye is not None:
            self.right_eye = true_matrix.map(self.right_eye)

        if self.head_top is not None:
            self.head_top = true_matrix.map(self.head_top)
        if self.chin is not None:
            self.chin = true_matrix.map(self.chin)
        if self.face_rect is not None:
            self.face_rect = true_matrix.mapRect(self.face_rect)

        crop_center = true_matrix.map(old_crop.center())
        self.crop_rect = QRectF(
            crop_center.x() - old_crop.width() / 2,
            crop_center.y() - old_crop.height() / 2,
            old_crop.width(),
            old_crop.height()
        )

        self.pixmap = rotated_pixmap
        self.rotation_angle -= angle

        # Кадр может выходить за пределы фотографии; не меняем его размер
        # и положение после выравнивания.
        self.update()
        return True

    def align_vertical_axis(self):
        """Выравнивает вертикальную ось и центрирует её в кадре."""
        if (
            self.pixmap.isNull()
            or self.head_top is None
            or self.chin is None
        ):
            return False

        dx = self.chin.x() - self.head_top.x()
        dy = self.chin.y() - self.head_top.y()

        if math.hypot(dx, dy) < 1:
            return False

        angle = math.degrees(math.atan2(dy, dx))
        rotation_delta = 90.0 - angle

        old_crop = QRectF(self.crop_rect)

        transform = QTransform()
        transform.rotate(rotation_delta)

        true_matrix = QImage.trueMatrix(
            transform,
            self.pixmap.width(),
            self.pixmap.height()
        )

        transformed_pixmap = self.pixmap.transformed(
            transform,
            Qt.TransformationMode.SmoothTransformation
        )
        rotated_pixmap = QPixmap(transformed_pixmap.size())
        rotated_pixmap.fill(QColor("white"))
        painter = QPainter(rotated_pixmap)
        painter.drawPixmap(QPointF(0, 0), transformed_pixmap)
        painter.end()

        # Поворачиваем все имеющиеся метки.
        if self.left_eye is not None:
            self.left_eye = true_matrix.map(self.left_eye)
        if self.right_eye is not None:
            self.right_eye = true_matrix.map(self.right_eye)
        self.head_top = true_matrix.map(self.head_top)
        self.chin = true_matrix.map(self.chin)

        if self.face_rect is not None:
            self.face_rect = true_matrix.mapRect(self.face_rect)

        # Находим середину оси макушка-подбородок.
        axis_center_x = (
            self.head_top.x() + self.chin.x()
        ) / 2

        # Центр рамки кадрирования после поворота.
        crop_center = true_matrix.map(old_crop.center())

        # Сколько нужно переместить фотографию по горизонтали.
        shift_x = crop_center.x() - axis_center_x

        # При необходимости добавляем свободное место слева.
        pad_left = max(0, math.ceil(-shift_x))

        # Итоговое положение фотографии на новом полотне.
        draw_x = pad_left + shift_x

        new_width = int(math.ceil(
            max(
                pad_left + self.pixmap.width(),
                draw_x + rotated_pixmap.width()
            )
        ))

        # Создаём новое полотно, чтобы не обрезать фотографию.
        canvas = QPixmap(new_width, rotated_pixmap.height())
        canvas.fill(QColor("white"))

        painter = QPainter(canvas)
        painter.drawPixmap(
            QPointF(draw_x, 0),
            rotated_pixmap
        )
        painter.end()

        # Перемещаем метки вместе с фотографией.
        offset = QPointF(draw_x, 0)

        if self.left_eye is not None:
            self.left_eye += offset
        if self.right_eye is not None:
            self.right_eye += offset

        self.head_top += offset
        self.chin += offset

        if self.face_rect is not None:
            self.face_rect.translate(draw_x, 0)

        # Рамка остаётся на прежнем месте относительно кадра.
        crop_center.setX(crop_center.x() + pad_left)

        self.crop_rect = QRectF(
            crop_center.x() - old_crop.width() / 2,
            crop_center.y() - old_crop.height() / 2,
            old_crop.width(),
            old_crop.height()
        )

        self.pixmap = canvas
        self.rotation_angle += rotation_delta

        self.update()
        return True
        
    def set_markup_enabled(self, enabled):
        self.markup_enabled = enabled
        self.update()

    def set_tool(self, tool):
        self.active_tool = tool
        self.update()


    def set_aspect_ratio(self, width, height):
        # Сохраняем прежнюю рамку, чтобы смена формата
        # не переносила её в центр фотографии.
        old_crop = QRectF(self.crop_rect)

        if width > 0 and height > 0:
            self.format_width_mm = float(width)
            self.format_height_mm = float(height)
            self.aspect_ratio = self.format_width_mm / self.format_height_mm
        else:
            self.format_width_mm = 35.0
            self.format_height_mm = 45.0
            self.aspect_ratio = 35 / 45

        self.margin_fit_applied = False

        # Если фотографии нет, сохраняем только пропорции формата.
        if self.pixmap.isNull():
            self.crop_rect = QRectF()
            self.crop_ratio_locked = True
            self.update()
            return

        image_width = self.pixmap.width()
        image_height = self.pixmap.height()

        # Если прежней рамки нет, создаём начальную.
        if old_crop.isNull() or old_crop.width() <= 0 or old_crop.height() <= 0:
            self.create_initial_crop()
            self.crop_ratio_locked = True
            self.update()
            return

        # Сохраняем прежний центр рамки.
        center = old_crop.center()

        # Вписываем новые пропорции в прежние габариты рамки.
        new_width = old_crop.width()
        new_height = new_width / self.aspect_ratio

        if new_height > old_crop.height():
            new_height = old_crop.height()
            new_width = new_height * self.aspect_ratio

        # Сохраняем размер и центр рамки относительно старой композиции.
        # Рамка может выходить за края изображения.
        x = center.x() - new_width / 2
        y = center.y() - new_height / 2

        self.crop_rect = QRectF(x, y, new_width, new_height)

        # После выбора формата пропорции рамки зафиксированы.
        self.crop_ratio_locked = True

        self.update()


    def create_initial_crop(self):
        if self.pixmap.isNull():
            self.crop_rect = QRectF()
            return
        if not hasattr(self, "aspect_ratio"):
            self.aspect_ratio = 35 / 45
        image_width = self.pixmap.width()
        image_height = self.pixmap.height()
        crop_height = image_height * 0.78
        crop_width = crop_height * self.aspect_ratio
        if crop_width > image_width * 0.9:
            crop_width = image_width * 0.9
            crop_height = crop_width / self.aspect_ratio
        x = (image_width - crop_width) / 2
        y = (image_height - crop_height) / 2
        self.crop_rect = QRectF(x, y, crop_width, crop_height)

    def reset_marks(self):
        self.left_eye = None
        self.right_eye = None
        self.head_top = None
        self.chin = None
        self.face_rect = None
        self.update()

    def resizeEvent(self, event):
        self.update()
        super().resizeEvent(event)


    def draw_format_corner(self, painter, frame):
        """Рисует белый уголок выбранного формата в заданном прямоугольнике."""
        if not (
            self.corner_enabled
            and self.corner_type in (4, 5)
            and self.corner_width_mm > 0
            and self.corner_height_mm > 0
            and self.format_width_mm > 0
            and self.format_height_mm > 0
        ):
            return

        corner_w = frame.width() * self.corner_width_mm / self.format_width_mm
        corner_h = frame.height() * self.corner_height_mm / self.format_height_mm
        corner_x = frame.left() if self.corner_type == 4 else frame.right()
        corner_y = frame.bottom()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))

        corner_path = QPainterPath()
        if self.corner_shape == "Закруглённый срез":
            arc_rect = QRectF(
                corner_x - corner_w,
                corner_y - corner_h,
                corner_w * 2,
                corner_h * 2
            )
            if self.corner_type == 4:
                corner_path.moveTo(corner_x, corner_y)
                corner_path.lineTo(corner_x + corner_w, corner_y)
                corner_path.arcTo(arc_rect, 0, 90)
            else:
                corner_path.moveTo(corner_x, corner_y)
                corner_path.lineTo(corner_x, corner_y - corner_h)
                corner_path.arcTo(arc_rect, 90, 90)
        elif self.corner_type == 4:
            corner_path.moveTo(corner_x, corner_y)
            corner_path.lineTo(corner_x + corner_w, corner_y)
            corner_path.lineTo(corner_x, corner_y - corner_h)
        else:
            corner_path.moveTo(corner_x, corner_y)
            corner_path.lineTo(corner_x - corner_w, corner_y)
            corner_path.lineTo(corner_x, corner_y - corner_h)

        corner_path.closeSubpath()
        painter.drawPath(corner_path)
        painter.restore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(45, 45, 45))

        if self.pixmap.isNull():
            painter.setPen(QColor(220, 220, 220))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Откройте фотографию"
            )
            return

        # Рассчитываем масштаб так, чтобы фотография целиком
        # занимала максимально возможную площадь рабочей области.
        # Пропорции фотографии при этом сохраняются.

        image_width = self.pixmap.width()
        image_height = self.pixmap.height()

        area_width = max(1, self.width())
        area_height = max(1, self.height())

        self.fit_scale = min(
            area_width / image_width,
            area_height / image_height
        )

        # Учитываем ручное масштабирование пользователя.
        draw_width = image_width * self.fit_scale * self.zoom
        draw_height = image_height * self.fit_scale * self.zoom

        # Центрируем фотографию в рабочей области.
        # Ручное перемещение учитывается отдельно.
        x = (self.width() - draw_width) / 2 + self.pan_offset.x()
        y = (self.height() - draw_height) / 2 + self.pan_offset.y()

        target = QRectF(
            x,
            y,
            draw_width,
            draw_height
        )
        self.image_rect = target

        painter.drawPixmap(
            target,
            self.pixmap,
            QRectF(self.pixmap.rect())
        )

        # Отображаем удалённые участки фотографии красным цветом.
        # Маска содержит прозрачность: 255 — оставить, 0 — удалить.
        if (
            self.background_mask is not None
            and self.background_brush_enabled
        ):
            mask_height, mask_width = self.background_mask.shape

            mask_image = QImage(
                self.background_mask.data,
                mask_width,
                mask_height,
                self.background_mask.strides[0],
                QImage.Format.Format_Grayscale8
            ).copy()

            # Инвертируем маску: красным показываем удалённые участки.
            mask_image.invertPixels()

            overlay = QImage(
                mask_width,
                mask_height,
                QImage.Format.Format_ARGB32
            )
            overlay.fill(QColor(255, 50, 50, 110))
            overlay.setAlphaChannel(mask_image)

            painter.drawImage(target, overlay)

        # Отображаем контур кисти при ручной коррекции.
        if self.background_brush_enabled:
            scale_x = target.width() / self.pixmap.width()
            scale_y = target.height() / self.pixmap.height()

            brush_diameter = (
                self.background_brush_size
                * (scale_x + scale_y)
                / 2
            )

            cursor_pos = self.mapFromGlobal(
                self.cursor().pos()
            )

            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.drawEllipse(
                cursor_pos,
                brush_diameter / 2,
                brush_diameter / 2
            )

        if not self.markup_enabled or self.crop_rect.isEmpty():
            return

        scale_x = target.width() / self.pixmap.width()
        scale_y = target.height() / self.pixmap.height()

        frame = QRectF(
            target.x() + self.crop_rect.x() * scale_x,
            target.y() + self.crop_rect.y() * scale_y,
            self.crop_rect.width() * scale_x,
            self.crop_rect.height() * scale_y
        )

        path = QPainterPath()
        path.addRect(target)

        hole = QPainterPath()
        hole.addRect(frame)

        painter.fillPath(
            path.subtracted(hole),
            QColor(0, 0, 0, 145)
        )

        # Уголок формата рисуется тем же методом, что и в рабочей копии.
        self.draw_format_corner(painter, frame)

        painter.setPen(QPen(QColor(80, 255, 120), 2))
        painter.drawRect(frame)

        if self.show_markup_points:

            painter.setPen(
                QPen(
                    QColor(255, 255, 255, 150),
                    1,
                    Qt.PenStyle.DashLine
                )
            )

            painter.drawLine(
                QPointF(frame.center().x(), frame.top()),
                QPointF(frame.center().x(), frame.bottom())
            )

            painter.drawLine(
                QPointF(frame.left(), frame.center().y()),
                QPointF(frame.right(), frame.center().y())
            )

            if self.left_eye is not None and self.right_eye is not None:
                left_x = self.image_rect.x() + self.left_eye.x() * scale_x
                left_y = self.image_rect.y() + self.left_eye.y() * scale_y
                right_x = self.image_rect.x() + self.right_eye.x() * scale_x
                right_y = self.image_rect.y() + self.right_eye.y() * scale_y

                painter.setPen(
                    QPen(
                        QColor(255, 220, 0, 220),
                        2,
                        Qt.PenStyle.DashLine
                    )
                )

                painter.drawLine(
                    QPointF(left_x, left_y),
                    QPointF(right_x, right_y)
                )

            self.draw_eye_marker(
                painter, self.left_eye, scale_x, scale_y, "1"
            )

            self.draw_eye_marker(
                painter, self.right_eye, scale_x, scale_y, "2"
            )

            if self.head_top is not None and self.chin is not None:
                top_pos = QPointF(
                    self.image_rect.x() + self.head_top.x() * scale_x,
                    self.image_rect.y() + self.head_top.y() * scale_y
                )

                chin_pos = QPointF(
                    self.image_rect.x() + self.chin.x() * scale_x,
                    self.image_rect.y() + self.chin.y() * scale_y
                )

                painter.setPen(
                    QPen(
                        QColor(0, 220, 255, 230),
                        2,
                        Qt.PenStyle.DashLine
                    )
                )

                painter.drawLine(top_pos, chin_pos)

                painter.setPen(
                    QPen(
                        QColor(0, 220, 255, 190),
                        1,
                        Qt.PenStyle.DashLine
                    )
                )

                painter.drawLine(
                    QPointF(self.image_rect.left(), top_pos.y()),
                    QPointF(self.image_rect.right(), top_pos.y())
                )

                painter.drawLine(
                    QPointF(self.image_rect.left(), chin_pos.y()),
                    QPointF(self.image_rect.right(), chin_pos.y())
                )

            for point, label in (
                (self.head_top, "В"),
                (self.chin, "Пд")
            ):
                if point is not None:
                    marker = QPointF(
                        self.image_rect.x() + point.x() * scale_x,
                        self.image_rect.y() + point.y() * scale_y
                    )

                    painter.setPen(QPen(QColor(0, 220, 255), 2))
                    painter.setBrush(QColor(0, 80, 100, 210))
                    painter.drawEllipse(marker, 8, 8)

                    painter.setPen(QColor(255, 255, 255))
                    painter.drawText(
                        marker + QPointF(11, -8),
                        label
                    )

        if self.hover_eye is not None:
            eye_point = (
                self.left_eye
                if self.hover_eye == "left_eye"
                else self.right_eye
            )

            if eye_point is not None:
                source_size = self.magnifier_size / self.magnifier_zoom

                source_rect = QRectF(
                    eye_point.x() - source_size / 2,
                    eye_point.y() - source_size / 2,
                    source_size,
                    source_size
                )

                source_rect = source_rect.intersected(
                    QRectF(self.pixmap.rect())
                )

                magnifier_size = self.magnifier_size

                magnifier_rect = QRectF(
                    self.magnifier_position.x(),
                    self.magnifier_position.y(),
                    magnifier_size,
                    magnifier_size
                )

                painter.save()

                clip_path = QPainterPath()
                clip_path.addEllipse(magnifier_rect)
                painter.setClipPath(clip_path)

                painter.fillRect(
                    magnifier_rect,
                    QColor(30, 30, 30)
                )

                painter.drawPixmap(
                    magnifier_rect,
                    self.pixmap,
                    source_rect
                )

                painter.setPen(QPen(QColor(255, 60, 60), 1))

                painter.drawLine(
                    magnifier_rect.center().x() - 12,
                    magnifier_rect.center().y(),
                    magnifier_rect.center().x() + 12,
                    magnifier_rect.center().y()
                )

                painter.drawLine(
                    magnifier_rect.center().x(),
                    magnifier_rect.center().y() - 12,
                    magnifier_rect.center().x(),
                    magnifier_rect.center().y() + 12
                )

                painter.restore()

                painter.setPen(QPen(QColor(255, 255, 255), 2))
                painter.drawEllipse(magnifier_rect)

                painter.setPen(QColor(255, 255, 255))

                painter.drawText(
                    QRectF(
                        magnifier_rect.x(),
                        magnifier_rect.bottom() + 5,
                        220,
                        22
                    ),
                    Qt.AlignmentFlag.AlignLeft,
                    f"X: {eye_point.x():.1f}  Y: {eye_point.y():.1f}  ×{self.magnifier_zoom}"
                )


    def draw_eye_marker(self, painter, point, scale_x, scale_y, label):
        if point is None:
            return
        x = self.image_rect.x() + point.x() * scale_x
        y = self.image_rect.y() + point.y() * scale_y
        painter.setPen(QPen(QColor(255, 220, 0), 2))
        painter.setBrush(QColor(255, 220, 0, 70))
        painter.drawEllipse(QPointF(x, y), 7, 7)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(
            QPointF(x + 9, y - 5),
            label
        )

    # Определяем, на какую часть рамки наведён курсор.
    def get_frame_resize_handle(self, pos):
        if self.crop_rect.isEmpty() or self.image_rect.isEmpty():
            return None

        scale_x = self.image_rect.width() / self.pixmap.width()
        scale_y = self.image_rect.height() / self.pixmap.height()

        frame = QRectF(
            self.image_rect.x() + self.crop_rect.x() * scale_x,
            self.image_rect.y() + self.crop_rect.y() * scale_y,
            self.crop_rect.width() * scale_x,
            self.crop_rect.height() * scale_y
        )

        # Размер зоны захвата углов и сторон в экранных пикселях.
        handle_size = 12

        left = abs(pos.x() - frame.left()) <= handle_size
        right = abs(pos.x() - frame.right()) <= handle_size
        top = abs(pos.y() - frame.top()) <= handle_size
        bottom = abs(pos.y() - frame.bottom()) <= handle_size

        # Сначала проверяем углы.
        if left and top:
            return "top_left"
        if right and top:
            return "top_right"
        if left and bottom:
            return "bottom_left"
        if right and bottom:
            return "bottom_right"

        # Затем стороны.
        if left and frame.top() <= pos.y() <= frame.bottom():
            return "left"
        if right and frame.top() <= pos.y() <= frame.bottom():
            return "right"
        if top and frame.left() <= pos.x() <= frame.right():
            return "top"
        if bottom and frame.left() <= pos.x() <= frame.right():
            return "bottom"

        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.pan_dragging = True
            self.pan_start = event.position()
            return

        # Ручная коррекция маски фона левой кнопкой мыши.
        # Проверяем режим кисти до ограничений режима разметки.
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.background_brush_enabled
            and not self.pixmap.isNull()
            and self.image_rect.contains(event.position())
        ):
            pos = event.position()

            scale_x = self.image_rect.width() / self.pixmap.width()
            scale_y = self.image_rect.height() / self.pixmap.height()

            image_x = (pos.x() - self.image_rect.x()) / scale_x
            image_y = (pos.y() - self.image_rect.y()) / scale_y

            self.background_brush_dragging = True
            self.background_brush_last_pos = QPointF(
                image_x,
                image_y
            )

            # Передаём первую точку мазка в главное окно.
            if self.background_brush_callback is not None:
                self.background_brush_callback(
                    None,
                    self.background_brush_last_pos,
                    self.background_brush_type,
                    self.background_brush_size,
                    self.background_brush_softness
                )

            self.update()
            return

        if (
            not self.markup_enabled
            or self.composition_locked
            or self.pixmap.isNull()
        ):
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.position()

        if self.hover_eye is not None:
            magnifier_rect = QRectF(
                self.magnifier_position.x(),
                self.magnifier_position.y(),
                self.magnifier_size,
                self.magnifier_size
            )
            if magnifier_rect.contains(pos):
                self.dragging_magnifier = True
                self.magnifier_drag_offset = (
                    pos - self.magnifier_position
                )
                return        

        if not self.image_rect.contains(pos):
            return

        scale_x = self.image_rect.width() / self.pixmap.width()
        scale_y = self.image_rect.height() / self.pixmap.height()

        for marker_name, marker_point in (
            ("left_eye", self.left_eye),
            ("right_eye", self.right_eye),
            ("head_top", self.head_top),
            ("chin", self.chin)
        ):
            if marker_point is not None:
                marker_x = self.image_rect.x() + marker_point.x() * scale_x
                marker_y = self.image_rect.y() + marker_point.y() * scale_y
                if abs(pos.x() - marker_x) < 16 and abs(pos.y() - marker_y) < 16:
                    self.dragging_eye = marker_name
                    self.hover_eye = marker_name if marker_name in ("left_eye", "right_eye") else None
                    return

        image_x = (pos.x() - self.image_rect.x()) / scale_x
        image_y = (pos.y() - self.image_rect.y()) / scale_y

        # Захват угла или стороны рамки для изменения её размера.
        if self.active_tool == "move":
            handle = self.get_frame_resize_handle(pos)

            if handle is not None:
                self.frame_resize_handle = handle
                self.frame_resize_start_rect = QRectF(self.crop_rect)
                self.frame_resize_start_pos = QPointF(image_x, image_y)

                modifiers = event.modifiers()
                alt_pressed = bool(
                    modifiers & Qt.KeyboardModifier.AltModifier
                )
                shift_pressed = bool(
                    modifiers & Qt.KeyboardModifier.ShiftModifier
                )

                self.frame_resize_mode = (
                    "center_proportional"
                    if alt_pressed and shift_pressed
                    else "free"
                )

                return

        # Установка левого глаза.
        # После щелчка автоматически возвращаемся к перемещению рамки.
        if self.active_tool == "left_eye":
            if self.crop_rect.contains(image_x, image_y):
                self.left_eye = QPointF(image_x, image_y)
                self.update()
                self.set_tool("move")
            return

        # Установка правого глаза.
        if self.active_tool == "right_eye":
            if self.crop_rect.contains(image_x, image_y):
                self.right_eye = QPointF(image_x, image_y)
                self.update()
                self.set_tool("move")
            return

        # Установка макушки.
        # Точка может находиться за пределами рамки кадрирования.
        if self.active_tool == "head_top":
            self.head_top = QPointF(image_x, image_y)
            self.update()
            self.set_tool("move")
            return

        # Установка подбородка.
        # Точка может находиться за пределами рамки кадрирования.
        if self.active_tool == "chin":
            self.chin = QPointF(image_x, image_y)
            self.update()
            self.set_tool("move")
            return

        if self.crop_rect.contains(image_x, image_y):
            self.dragging = True
            self.drag_offset = QPointF(
                image_x - self.crop_rect.x(),
                image_y - self.crop_rect.y()
            )

            # Запоминаем исходное положение рамки для перемещения с Shift.
            self.drag_start_pos = QPointF(image_x, image_y)
            self.drag_start_rect = QRectF(self.crop_rect)
            self.drag_axis = None

    def mouseMoveEvent(self, event):
        pos = event.position()
        # Меняем курсор в зависимости от положения относительно рамки.
        if (
            self.markup_enabled
            and not self.composition_locked
            and self.pixmap.isNull() is False
            and not self.dragging
            and self.dragging_eye is None
            and not self.pan_dragging
        ):
            handle = self.get_frame_resize_handle(pos)

            cursors = {
                "top_left": Qt.CursorShape.SizeFDiagCursor,
                "bottom_right": Qt.CursorShape.SizeFDiagCursor,
                "top_right": Qt.CursorShape.SizeBDiagCursor,
                "bottom_left": Qt.CursorShape.SizeBDiagCursor,
                "left": Qt.CursorShape.SizeHorCursor,
                "right": Qt.CursorShape.SizeHorCursor,
                "top": Qt.CursorShape.SizeVerCursor,
                "bottom": Qt.CursorShape.SizeVerCursor,
            }

            if handle:
                self.setCursor(cursors[handle])
            elif self.crop_rect.contains(
                (pos.x() - self.image_rect.x())
                / (self.image_rect.width() / self.pixmap.width()),
                (pos.y() - self.image_rect.y())
                / (self.image_rect.height() / self.pixmap.height())
            ):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

        if self.dragging_magnifier:
            new_pos = pos - self.magnifier_drag_offset
            max_x = self.width() - self.magnifier_size
            max_y = self.height() - self.magnifier_size
            new_x = max(0, min(new_pos.x(), max_x))
            new_y = max(0, min(new_pos.y(), max_y))
            self.magnifier_position = QPointF(new_x, new_y)
            self.update()
            return

        if self.pan_dragging:
            delta = pos - self.pan_start
            self.pan_offset += delta
            self.pan_start = pos
            self.update()
            return

        # Непрерывное рисование маски при движении мыши.
        if (
            self.background_brush_enabled
            and self.background_brush_dragging
            and not self.pixmap.isNull()
        ):
            # Ограничиваем движение границами фотографии.
            brush_x = max(
                self.image_rect.left(),
                min(pos.x(), self.image_rect.right())
            )
            brush_y = max(
                self.image_rect.top(),
                min(pos.y(), self.image_rect.bottom())
            )

            scale_x = self.image_rect.width() / self.pixmap.width()
            scale_y = self.image_rect.height() / self.pixmap.height()

            image_x = (brush_x - self.image_rect.x()) / scale_x
            image_y = (brush_y - self.image_rect.y()) / scale_y

            current_pos = QPointF(image_x, image_y)

            # Передаём предыдущую и текущую точки для непрерывного мазка.
            if self.background_brush_callback is not None:
                self.background_brush_callback(
                    self.background_brush_last_pos,
                    current_pos,
                    self.background_brush_type,
                    self.background_brush_size,
                    self.background_brush_softness
                )

            self.background_brush_last_pos = current_pos
            self.update()
            return

        if self.pixmap.isNull():
            return

        # Не прерываем перетаскивание рамки, если курсор вышел за фотографию.
        if (
            not self.image_rect.contains(pos)
            and self.frame_resize_handle is None
            and self.dragging_eye is None
        ):
            self.hover_eye = None
            self.update()
            return

        scale_x = self.image_rect.width() / self.pixmap.width()
        scale_y = self.image_rect.height() / self.pixmap.height()

        # Вычисляем свободные координаты курсора
        # относительно исходной фотографии.
        # Они могут выходить за её границы — это нужно для рамки.
        image_x = (
            pos.x() - self.image_rect.x()
        ) / scale_x

        image_y = (
            pos.y() - self.image_rect.y()
        ) / scale_y

        # Отдельно ограничиваем координаты для отметок лица и глаз.
        marker_x = max(0, min(image_x, self.pixmap.width()))
        marker_y = max(0, min(image_y, self.pixmap.height()))


        # Изменяем рамку при перетаскивании угла или стороны.
        if self.frame_resize_handle is not None:
            start = self.frame_resize_start_rect

            left = start.left()
            right = start.right()
            top = start.top()
            bottom = start.bottom()

            handle = self.frame_resize_handle
            min_size = 20

            # Определяем пропорции рамки.
            if self.crop_ratio_locked:
                ratio = self.format_width_mm / self.format_height_mm
            else:
                ratio = start.width() / max(start.height(), 0.001)

            # Alt + Shift: пропорциональное изменение относительно центра.
            if self.frame_resize_mode == "center_proportional":
                center = start.center()

                if handle in ("left", "right"):
                    new_width = abs(image_x - center.x()) * 2
                    new_height = new_width / ratio

                elif handle in ("top", "bottom"):
                    new_height = abs(image_y - center.y()) * 2
                    new_width = new_height * ratio

                else:
                    new_width = abs(image_x - center.x()) * 2
                    new_height = abs(image_y - center.y()) * 2

                    if new_width / max(new_height, 0.001) > ratio:
                        new_height = new_width / ratio
                    else:
                        new_width = new_height * ratio

                new_width = max(min_size, new_width)
                new_height = new_width / ratio

                left = center.x() - new_width / 2
                right = center.x() + new_width / 2
                top = center.y() - new_height / 2
                bottom = center.y() + new_height / 2

            # Изменение с сохранением пропорций выбранного формата.
            elif self.crop_ratio_locked:

                # Перетаскивание угла.
                if handle in (
                    "top_left", "top_right",
                    "bottom_left", "bottom_right"
                ):
                    anchor_x = (
                        start.left() if "right" in handle
                        else start.right()
                    )
                    anchor_y = (
                        start.top() if "bottom" in handle
                        else start.bottom()
                    )

                    new_width = max(
                        abs(image_x - anchor_x),
                        abs(image_y - anchor_y) * ratio,
                        min_size
                    )
                    new_height = new_width / ratio

                    left = (
                        anchor_x if "right" in handle
                        else anchor_x - new_width
                    )
                    right = left + new_width

                    top = (
                        anchor_y if "bottom" in handle
                        else anchor_y - new_height
                    )
                    bottom = top + new_height

                # Левая или правая сторона.
                elif handle in ("left", "right"):
                    center_y = start.center().y()

                    anchor_x = (
                        start.right() if handle == "left"
                        else start.left()
                    )

                    new_width = max(
                        min_size,
                        abs(image_x - anchor_x)
                    )
                    new_height = new_width / ratio

                    left = (
                        anchor_x - new_width
                        if handle == "left"
                        else anchor_x
                    )
                    right = left + new_width

                    top = center_y - new_height / 2
                    bottom = center_y + new_height / 2

                # Верхняя или нижняя сторона.
                elif handle in ("top", "bottom"):
                    center_x = start.center().x()

                    anchor_y = (
                        start.bottom() if handle == "top"
                        else start.top()
                    )

                    new_height = max(
                        min_size / ratio,
                        abs(image_y - anchor_y)
                    )
                    new_width = new_height * ratio

                    top = (
                        anchor_y - new_height
                        if handle == "top"
                        else anchor_y
                    )
                    bottom = top + new_height

                    left = center_x - new_width / 2
                    right = center_x + new_width / 2

            # Свободное изменение размера без ограничений по фотографии.
            else:
                if "left" in handle:
                    left = min(
                        image_x,
                        start.right() - min_size
                    )

                if "right" in handle:
                    right = max(
                        image_x,
                        start.left() + min_size
                    )

                if "top" in handle:
                    top = min(
                        image_y,
                        start.bottom() - min_size
                    )

                if "bottom" in handle:
                    bottom = max(
                        image_y,
                        start.top() + min_size
                    )

            # Сохраняем новую геометрию рамки.
            self.crop_rect = QRectF(
                left,
                top,
                right - left,
                bottom - top
            )

            self.margin_fit_applied = False
            self.update()
            return

        if self.dragging_eye is not None and not self.composition_locked:
            image_x = marker_x
            image_y = marker_y
            if self.dragging_eye == "left_eye":
                self.left_eye = QPointF(image_x, image_y)
            elif self.dragging_eye == "right_eye":
                self.right_eye = QPointF(image_x, image_y)
            elif self.dragging_eye == "head_top":
                self.head_top = QPointF(image_x, image_y)
            elif self.dragging_eye == "chin":
                self.chin = QPointF(image_x, image_y)
            self.update()
            return

        self.hover_eye = None
        for eye_name, eye_point in (
            ("left_eye", self.left_eye),
            ("right_eye", self.right_eye)
        ):
            if eye_point is not None:
                eye_x = self.image_rect.x() + eye_point.x() * scale_x
                eye_y = self.image_rect.y() + eye_point.y() * scale_y
                if abs(pos.x() - eye_x) < 16 and abs(pos.y() - eye_y) < 16:
                    self.hover_eye = eye_name
                    break

        if not self.dragging or self.composition_locked:
            self.update()
            return

        new_x = image_x - self.drag_offset.x()
        new_y = image_y - self.drag_offset.y()

        # Shift ограничивает перемещение рамки одной осью.
        modifiers = event.modifiers()
        shift_pressed = bool(
            modifiers & Qt.KeyboardModifier.ShiftModifier
        )

        if shift_pressed:
            dx = image_x - self.drag_start_pos.x()
            dy = image_y - self.drag_start_pos.y()

            # Определяем направление по первому заметному движению.
            if self.drag_axis is None and max(abs(dx), abs(dy)) >= 3:
                self.drag_axis = "horizontal" if abs(dx) >= abs(dy) else "vertical"

            if self.drag_axis == "horizontal":
                new_y = self.drag_start_rect.y()
            elif self.drag_axis == "vertical":
                new_x = self.drag_start_rect.x()
        else:
            self.drag_axis = None

        # Разрешаем свободно перемещать рамку,
        # в том числе за пределы исходной фотографии.
        self.crop_rect.moveTo(new_x, new_y)
        self.margin_fit_applied = False
        self.update()

    def mouseReleaseEvent(self, event):
        # Завершение всех действий, выполняемых левой кнопкой мыши.
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            # Завершаем ручную коррекцию маски.
            self.background_brush_dragging = False
            self.background_brush_last_pos = None
            self.dragging_eye = None
            self.dragging_magnifier = False

            # Завершаем изменение размера рамки.
            self.frame_resize_handle = None
            self.frame_resize_mode = None

        # Завершение перемещения фотографии правой кнопкой мыши.
        if event.button() == Qt.MouseButton.RightButton:
            self.pan_dragging = False

        self.update()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if self.pixmap.isNull():
                event.ignore()
                return
            delta = event.angleDelta().y()
            if delta == 0:
                event.ignore()
                return
            old_zoom = self.zoom
            new_zoom = old_zoom * (1.15 ** (delta / 120.0))
            new_zoom = max(0.25, min(8.0, new_zoom))
            if new_zoom != old_zoom:
                cursor = event.position()
                center = QPointF(
                    self.width() / 2,
                    self.height() / 2
                )
                self.pan_offset = (
                    cursor - center
                    - (cursor - center - self.pan_offset)
                    * (new_zoom / old_zoom)
                )
                self.zoom = new_zoom
                self.update()
            event.accept()
            return

        if self.hover_eye is None:
            event.ignore()
            return

        if event.angleDelta().y() > 0:
            self.magnifier_zoom = min(10, self.magnifier_zoom + 1)
        else:
            self.magnifier_zoom = max(2, self.magnifier_zoom - 1)
        self.update()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.double_click_callback is not None
        ):
            self.double_click_callback()
            return
        self.zoom = 1.0
        self.pan_offset = QPointF(0, 0)
        self.update()
