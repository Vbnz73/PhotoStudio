"""Расчёт размера и подготовка изображения без изменения рабочего документа.

Расчёты не требуют Qt. Для работы с QImage и PhotoDocument используются
PySide6 и NumPy из зависимостей проекта. Модуль не сохраняет файлы и не
обращается к интерфейсу редактора.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from fractions import Fraction
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    import numpy as np
    from PySide6.QtGui import QImage
    from photo_document import PhotoDocument

Number = Union[int, float, Decimal]

SUPPORTED_UNITS = frozenset({"mm", "cm", "in", "px"})
MIN_DPI = 1
MAX_DPI = 65_535
MIN_SIDE_PX = 1
MAX_SIDE_PX = 65_500
MAX_OUTPUT_PIXELS = 40_000_000
# Ограничение для одной копии QImage, включая возможное выравнивание строк.
MAX_IMAGE_BYTES = MAX_OUTPUT_PIXELS * 4
# Защита преобразования Decimal в Fraction от чрезмерно длинных чисел.
MAX_NUMBER_DIGITS = 1_000

_MM_PER_INCH = Decimal("25.4")
_DPI_PER_DPM = 0.0254
# За пределами этих значений результат известен без преобразования Decimal
# в Fraction. Это защищает от слишком больших положительных/отрицательных
# экспонент: малая положительная сторона всегда даёт один пиксель при ceil,
# а большая превышает MAX_SIDE_PX при любом допустимом DPI и любой единице.
_SMALL_PHYSICAL_INPUT = Decimal("0.000001")
_MAX_PHYSICAL_INPUT = Decimal("2000000")
_INCHES_PER_UNIT = {
    "mm": Fraction(5, 127),
    "cm": Fraction(50, 127),
    "in": Fraction(1, 1),
}


@dataclass(frozen=True)
class ExportSize:
    """Проверенные размеры, заданный DPI и целая плотность в точках/м."""

    width_px: int
    height_px: int
    dpi: int
    dots_per_meter: int


def _positive_decimal(value: Number, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{name}: требуется число")
    try:
        if isinstance(value, Decimal):
            number = value
        elif isinstance(value, int):
            number = Decimal(value)
        else:
            # str сохраняет привычное десятичное значение входного float.
            number = Decimal(str(value))
    except (InvalidOperation, OverflowError, ValueError) as error:
        raise ValueError(f"{name}: некорректное число") from error
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{name}: требуется положительное конечное число")
    if len(number.as_tuple().digits) > MAX_NUMBER_DIGITS:
        raise ValueError(f"{name}: число содержит более {MAX_NUMBER_DIGITS} цифр")
    return number


def _validated_dpi(dpi: Number) -> int:
    value = _positive_decimal(dpi, "DPI")
    if value < MIN_DPI or value > MAX_DPI or value != value.to_integral_value():
        raise ValueError(f"DPI должен быть целым числом от {MIN_DPI} до {MAX_DPI}")
    return int(value)


def _round_half_up(value: Fraction) -> int:
    """Округление неотрицательной дроби до ближайшего целого, половина вверх."""
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _pixel_side(value: Number, name: str) -> int:
    number = _positive_decimal(value, name)
    if number < MIN_SIDE_PX or number > MAX_SIDE_PX:
        raise ValueError(f"{name}: допустимы значения от {MIN_SIDE_PX} до {MAX_SIDE_PX} px")
    if number != number.to_integral_value():
        raise ValueError(f"{name}: число пикселей должно быть целым")
    return int(number)


def _physical_side_in_pixels(number: Decimal, units: str, dpi: int) -> Fraction:
    """Неокруглённая физическая сторона в пикселях."""
    return Fraction(number) * dpi * _INCHES_PER_UNIT[units]


def _output_side(value: Number, units: str, dpi: int, name: str) -> int:
    if units == "px":
        return _pixel_side(value, name)
    number = _positive_decimal(value, name)
    if number < _SMALL_PHYSICAL_INPUT:
        return MIN_SIDE_PX
    if number > _MAX_PHYSICAL_INPUT:
        raise ValueError(f"{name}: размер не может дать допустимое число пикселей")
    pixels = math.ceil(_physical_side_in_pixels(number, units, dpi))
    if not MIN_SIDE_PX <= pixels <= MAX_SIDE_PX:
        raise ValueError(f"{name}: после пересчёта сторона должна быть от {MIN_SIDE_PX} до {MAX_SIDE_PX} px")
    return pixels


def _validate_pixel_area(width_px: int, height_px: int) -> None:
    if not (MIN_SIDE_PX <= width_px <= MAX_SIDE_PX and
            MIN_SIDE_PX <= height_px <= MAX_SIDE_PX):
        raise ValueError("Сторона изображения выходит за допустимые пределы")
    if width_px * height_px > MAX_OUTPUT_PIXELS:
        raise ValueError(f"Изображение превышает предел {MAX_OUTPUT_PIXELS} пикселей")


def dpi_to_dots_per_meter(dpi: Number) -> int:
    """Перевести целый DPI в целое число точек на метр, половина вверх."""
    validated = _validated_dpi(dpi)
    return _round_half_up(Fraction(validated * 10_000, 254))


def calculate_export_size(width: Number, height: Number, units: str,
                          dpi: Number) -> ExportSize:
    """Рассчитать размеры для mm/cm/in/px без создания изображения.

    Физические размеры округляются вверх до целого пикселя. Повторный вызов
    с физическими размерами и другим DPI заново выбирает размер растра.
    Для px принимаются только целые значения, включая, например, 600.0.
    Чтобы изменить только DPI готового изображения, используйте set_image_dpi.
    """
    if not isinstance(units, str) or units not in SUPPORTED_UNITS:
        raise ValueError("Единица измерения должна быть mm, cm, in или px")
    validated_dpi = _validated_dpi(dpi)
    width_px = _output_side(width, units, validated_dpi, "Ширина")
    height_px = _output_side(height, units, validated_dpi, "Высота")
    _validate_pixel_area(width_px, height_px)
    return ExportSize(width_px, height_px, validated_dpi,
                      dpi_to_dots_per_meter(validated_dpi))


def estimate_print_size_mm(width_px: Number, height_px: Number,
                           dpi: Number) -> tuple[Decimal, Decimal]:
    """Приблизительный печатный размер при заданном DPI, без учёта кодека."""
    width = _pixel_side(width_px, "Ширина")
    height = _pixel_side(height_px, "Высота")
    _validate_pixel_area(width, height)
    validated_dpi = _validated_dpi(dpi)
    with localcontext(Context(prec=28, rounding=ROUND_HALF_UP)):
        return (Decimal(width) * _MM_PER_INCH / validated_dpi,
                Decimal(height) * _MM_PER_INCH / validated_dpi)


def _require_qimage(image: QImage) -> QImage:
    # Отложенный импорт позволяет использовать расчёты даже без установленного Qt.
    from PySide6.QtGui import QImage

    if not isinstance(image, QImage):
        raise TypeError("Ожидается QImage")
    if image.isNull():
        raise ValueError("QImage не содержит изображения")
    return image


def set_image_dpi(image: QImage, dpi: Number) -> QImage:
    """Изменить DPI готового растра без ресэмплинга и изменения пикселей.

    Возвращается независимая копия с одинаковой плотностью по обеим осям;
    переданный QImage не изменяется. Перед копированием проверяется объём
    его данных; сам модуль не записывает изображение на диск и не меняет
    возможные отдельные метаданные EXIF/XMP.
    """
    dpm = dpi_to_dots_per_meter(dpi)
    image = _require_qimage(image)
    _validate_pixel_area(image.width(), image.height())
    # Копия QImage выравнивает строки по 32 бита; у внешнего буфера
    # исходная строка может быть короче строки будущей копии.
    copy_stride = ((image.width() * image.depth() + 31) // 32) * 4
    copy_bytes = copy_stride * image.height()
    if max(image.sizeInBytes(), copy_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(f"Изображение превышает предел {MAX_IMAGE_BYTES} байт")
    result = image.copy()
    if result.isNull():
        raise MemoryError("Не удалось скопировать изображение")
    result.setDotsPerMeterX(dpm)
    result.setDotsPerMeterY(dpm)
    return result


def get_image_dpi(image: QImage) -> tuple[float, float]:
    """Прочитать DPI по X/Y из QImage с учётом округления точек на метр.

    Это значение объекта; наличие метаданных в исходном файле нужно
    проверять отдельно, поскольку Qt может подставить значение по умолчанию.
    """
    image = _require_qimage(image)
    x_dpm = image.dotsPerMeterX()
    y_dpm = image.dotsPerMeterY()
    if x_dpm <= 0 or y_dpm <= 0:
        raise ValueError("В QImage задана некорректная плотность")
    return x_dpm * _DPI_PER_DPM, y_dpm * _DPI_PER_DPM


def _resample_composite(image: QImage, target_width: int, target_height: int,
                        scale: Fraction) -> np.ndarray:
    """Один равномерный ресэмплинг premultiplied RGBA в массив NumPy.

    При уменьшении усредняется площадь пикселя, при увеличении используется
    билинейная интерполяция по его центру. На неполном крайнем пикселе площадь
    нормируется по доступной части изображения; отдельный фон не добавляется.
    """
    import numpy as np
    from PySide6.QtGui import QImage

    source = image.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    if source.isNull():
        raise MemoryError("Не удалось подготовить композит для масштабирования")
    source_width, source_height = source.width(), source.height()
    rows = np.frombuffer(source.constBits(), dtype=np.uint8,
                         count=source.sizeInBytes()).reshape(
                             source_height, source.bytesPerLine())
    pixels = rows[:, :source_width * 4].reshape(source_height, source_width, 4)
    output = np.empty((target_height, target_width, 4), dtype=np.uint8)
    factor = float(scale)  # Один и тот же коэффициент для обеих координат.

    if scale < 1:
        # Интегралы по строкам дают площадное усреднение с точностью float64,
        # включая дробные границы, без промежуточного изображения другого
        # размера и без выделения массива размером со весь исходный холст.
        x_edges = np.minimum(np.arange(target_width + 1, dtype=np.float64) / factor,
                             source_width)
        y_edges = np.minimum(np.arange(target_height + 1, dtype=np.float64) / factor,
                             source_height)
        x_index = np.floor(x_edges).astype(np.intp)
        x_fraction = x_edges - x_index
        x_sample = np.minimum(x_index, source_width - 1)
        x_widths = np.diff(x_edges)[:, None]
        prefix = np.empty((source_width + 1, 4), dtype=np.float64)
        prefix[0] = 0
        for y in range(target_height):
            top, bottom = float(y_edges[y]), float(y_edges[y + 1])
            accumulated = np.zeros((target_width, 4), dtype=np.float64)
            for source_y in range(math.floor(top), min(source_height, math.ceil(bottom))):
                weight = min(bottom, source_y + 1.0) - max(top, float(source_y))
                if weight <= 0:
                    continue
                row = pixels[source_y]
                np.cumsum(row, axis=0, dtype=np.float64, out=prefix[1:])
                integrals = prefix[x_index] + row[x_sample] * x_fraction[:, None]
                accumulated += np.diff(integrals, axis=0) * weight
            values = accumulated / (x_widths * (bottom - top))
            output[y] = np.clip(np.floor(values + 0.5), 0, 255).astype(np.uint8)
    else:
        # Центры пикселей преобразуются единым масштабом. На границе
        # билинейный фильтр берёт ближайший имеющийся исходный пиксель.
        x = np.clip((np.arange(target_width, dtype=np.float64) + 0.5) / factor - 0.5,
                    0, source_width - 1)
        y = np.clip((np.arange(target_height, dtype=np.float64) + 0.5) / factor - 0.5,
                    0, source_height - 1)
        x0 = np.floor(x).astype(np.intp)
        x1 = np.minimum(x0 + 1, source_width - 1)
        x_fraction = (x - x0)[:, None]
        y0 = np.floor(y).astype(np.intp)
        y1 = np.minimum(y0 + 1, source_height - 1)
        y_fraction = y - y0
        for target_y in range(target_height):
            top = (pixels[y0[target_y], x0] * (1 - x_fraction) +
                   pixels[y0[target_y], x1] * x_fraction)
            bottom = (pixels[y1[target_y], x0] * (1 - x_fraction) +
                      pixels[y1[target_y], x1] * x_fraction)
            values = top * (1 - y_fraction[target_y]) + bottom * y_fraction[target_y]
            output[target_y] = np.clip(np.floor(values + 0.5), 0, 255).astype(np.uint8)
    return output


def prepare_export_image(document: PhotoDocument, width: Number, height: Number,
                         units: str, dpi: Number, *, image_format: str = "png") -> QImage:
    """Подготовить независимый QImage без изменения документа и записи файлов.

    Физические размеры при каждом вызове выбираются заново; для изменения
    только DPI готового изображения следует использовать set_image_dpi.
    Композит целиком вписывается единым масштабом в целевой растр. Свободные
    целые строки/столбцы белые; они распределяются по центру, при нечётном
    остатке лишний пиксель оказывается справа или снизу. Дробный край
    дискретизируется существующим ресэмплером, без отдельного альфа-покрытия.

    PNG сохраняет прозрачность внутри композита; для JPEG прозрачные участки
    сводятся на белый. Формат определяет подготовку QImage, но запись файла
    остаётся обязанностью вызывающего кода. Функция не проверяет готовность
    редактора: документ должен быть стабильным во время создания снимка.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from photo_document import CompositeRenderer, PhotoDocument

    if not isinstance(document, PhotoDocument):
        raise TypeError("Ожидается PhotoDocument")
    if not isinstance(image_format, str) or image_format.lower() not in {"png", "jpeg", "jpg"}:
        raise ValueError("Формат изображения должен быть PNG или JPEG")
    image_format = image_format.lower()
    size = calculate_export_size(width, height, units, dpi)
    canvas = document.canvas_size
    try:
        _validate_pixel_area(canvas.width(), canvas.height())
    except ValueError as error:
        raise ValueError(f"Размер холста: {error}") from error
    scale = min(Fraction(size.width_px, canvas.width()),
                Fraction(size.height_px, canvas.height()))
    inner_width = math.ceil(canvas.width() * scale)
    inner_height = math.ceil(canvas.height() * scale)

    snapshot = document.copy()
    composite = CompositeRenderer().render(snapshot)
    del snapshot
    if composite.isNull():
        raise MemoryError("Не удалось создать композит документа")
    if composite.size() != canvas:
        raise RuntimeError("Размер композита не совпадает с холстом документа")

    if scale == 1:
        result = composite
    else:
        # Внутренний растр не содержит полностью пустых строк/столбцов:
        # при уменьшении у каждого пикселя есть ненулевая область выборки.
        pixels = _resample_composite(composite, inner_width, inner_height, scale)
        result = QImage(pixels.data, inner_width, inner_height, pixels.strides[0],
                        QImage.Format.Format_RGBA8888_Premultiplied)
        if result.isNull():
            raise MemoryError("Не удалось создать масштабированный композит")

    if inner_width != size.width_px or inner_height != size.height_px:
        padded = QImage(size.width_px, size.height_px,
                        QImage.Format.Format_RGBA8888_Premultiplied)
        if padded.isNull():
            raise MemoryError("Не удалось создать итоговое изображение")
        padded.fill(Qt.GlobalColor.white)
        painter = QPainter(padded)
        if not painter.isActive():
            raise RuntimeError("Не удалось разместить композит на холсте экспорта")
        try:
            # Source сохраняет прозрачность внутри композита, не смешивая её
            # с белым заполнением свободных областей.
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.drawImage((size.width_px - inner_width) // 2,
                              (size.height_px - inner_height) // 2, result)
        finally:
            painter.end()
        result = padded

    if image_format in {"jpeg", "jpg"}:
        opaque = QImage(size.width_px, size.height_px, QImage.Format.Format_RGB32)
        if opaque.isNull():
            raise MemoryError("Не удалось создать изображение для JPEG")
        opaque.fill(Qt.GlobalColor.white)
        painter = QPainter(opaque)
        if not painter.isActive():
            raise RuntimeError("Не удалось подготовить белую подложку для JPEG")
        try:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.drawImage(0, 0, result)
        finally:
            painter.end()
        result = opaque

    # set_image_dpi делает независимую копию до освобождения массива pixels.
    return set_image_dpi(result, size.dpi)
