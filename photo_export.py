"""Размеры для экспорта и плотность QImage без изменения рабочего документа.

Расчёты не требуют Qt. Для функций работы с QImage нужен PySide6, уже
указанный в зависимостях проекта. Этот модуль не сохраняет файлы и не
масштабирует изображения.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from fractions import Fraction
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

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
# Грубые пределы до преобразования Decimal в дробь: за ними сторона при
# любом допустимом DPI либо округлится в ноль, либо превысит MAX_SIDE_PX.
_MIN_PHYSICAL_INPUT = Decimal("0.000001")
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


def _output_side(value: Number, units: str, dpi: int, name: str) -> int:
    if units == "px":
        return _pixel_side(value, name)
    number = _positive_decimal(value, name)
    if number < _MIN_PHYSICAL_INPUT or number > _MAX_PHYSICAL_INPUT:
        raise ValueError(f"{name}: размер не может дать допустимое число пикселей")
    pixels = _round_half_up(Fraction(number) * dpi * _INCHES_PER_UNIT[units])
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

    Физические размеры округляются до пикселя с правилом половина вверх.
    Для px принимаются только целые значения, включая, например, 600.0.
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
    """Вернуть независимую копию QImage с DPI по обеим осям.

    Переданный QImage не изменяется. Перед копированием проверяется объём
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
