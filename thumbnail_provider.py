
# thumbnail_provider.py
# Получение миниатюр фотографий через Windows Shell API.
# Windows самостоятельно использует системный кэш миниатюр.

import os
import sys
import ctypes
from ctypes import wintypes

from PySide6.QtGui import QImage


# Этот модуль предназначен для Windows.
if sys.platform != "win32":
    raise RuntimeError(
        "Windows Thumbnail Provider доступен только в Windows."
    )


# ---------------------------------------------------------
# Структуры Windows API
# ---------------------------------------------------------

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SIZE(ctypes.Structure):
    _fields_ = [
        ("cx", ctypes.c_long),
        ("cy", ctypes.c_long),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 1),
    ]


# ---------------------------------------------------------
# Подключение системных библиотек Windows
# ---------------------------------------------------------

shell32 = ctypes.WinDLL("shell32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32")
gdi32 = ctypes.WinDLL("gdi32")
user32 = ctypes.WinDLL("user32")


# IID интерфейса IShellItemImageFactory
IID_ISHELLITEMIMAGEFACTORY = GUID(
    0xBCC18B79,
    0xBA16,
    0x442F,
    (ctypes.c_ubyte * 8)(
        0x80, 0xC4, 0x8A, 0x59,
        0xC3, 0x0C, 0x46, 0x3B
    )
)


# Параметры Windows Shell
SIIGBF_RESIZETOFIT = 0x00
SIIGBF_INCACHEONLY = 0x10


# ---------------------------------------------------------
# Получение функции GetImage из COM-интерфейса
# ---------------------------------------------------------

def _get_shell_image_factory(path):
    interface = ctypes.c_void_p()

    shell32.SHCreateItemFromParsingName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]

    shell32.SHCreateItemFromParsingName.restype = ctypes.c_long

    result = shell32.SHCreateItemFromParsingName(
        path,
        None,
        ctypes.byref(IID_ISHELLITEMIMAGEFACTORY),
        ctypes.byref(interface),
    )

    if result < 0 or not interface.value:
        return None

    return interface


# ---------------------------------------------------------
# Освобождение COM-интерфейса
# ---------------------------------------------------------

def _release_interface(interface):
    if not interface or not interface.value:
        return

    vtable = ctypes.cast(
        interface,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents

    release_function = ctypes.WINFUNCTYPE(
        wintypes.ULONG,
        ctypes.c_void_p
    )(vtable[2])

    release_function(interface)


# ---------------------------------------------------------
# Получение HBITMAP от Windows Shell
# ---------------------------------------------------------

def _get_hbitmap(interface, width, height, flags):
    vtable = ctypes.cast(
        interface,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents

    get_image = ctypes.WINFUNCTYPE(
        ctypes.c_long,
        ctypes.c_void_p,
        SIZE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HBITMAP)
    )(vtable[3])

    bitmap = wintypes.HBITMAP()

    result = get_image(
        interface,
        SIZE(width, height),
        flags,
        ctypes.byref(bitmap)
    )

    if result < 0 or not bitmap.value:
        return None

    return bitmap


# ---------------------------------------------------------
# Преобразование HBITMAP в QImage
# ---------------------------------------------------------

def _bitmap_to_qimage(bitmap):
    bitmap_info = BITMAPINFO()

    bitmap_info.bmiHeader.biSize = ctypes.sizeof(
        BITMAPINFOHEADER
    )

    bitmap_info.bmiHeader.biWidth = 0
    bitmap_info.bmiHeader.biHeight = 0

    # Получаем реальные размеры изображения.
    class BITMAP(ctypes.Structure):
        _fields_ = [
            ("bmType", ctypes.c_long),
            ("bmWidth", ctypes.c_long),
            ("bmHeight", ctypes.c_long),
            ("bmWidthBytes", ctypes.c_long),
            ("bmPlanes", ctypes.c_ushort),
            ("bmBitsPixel", ctypes.c_ushort),
            ("bmBits", ctypes.c_void_p),
        ]

    bmp = BITMAP()

    gdi32.GetObjectW.argtypes = [
        wintypes.HGDIOBJ,
        ctypes.c_int,
        ctypes.c_void_p,
    ]
    gdi32.GetObjectW.restype = ctypes.c_int

    if not gdi32.GetObjectW(
        bitmap,
        ctypes.sizeof(BITMAP),
        ctypes.byref(bmp)
    ):
        return None

    width = bmp.bmWidth
    height = bmp.bmHeight

    if width <= 0 or height <= 0:
        return None

    stride = width * 4
    buffer = ctypes.create_string_buffer(stride * height)

    bitmap_info.bmiHeader.biWidth = width
    bitmap_info.bmiHeader.biHeight = -height
    bitmap_info.bmiHeader.biPlanes = 1
    bitmap_info.bmiHeader.biBitCount = 32
    bitmap_info.bmiHeader.biCompression = 0
    bitmap_info.bmiHeader.biSizeImage = stride * height

    hdc = user32.GetDC(None)

    if not hdc:
        return None

    try:
        gdi32.GetDIBits.argtypes = [
            wintypes.HDC,
            wintypes.HBITMAP,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.POINTER(BITMAPINFO),
            wintypes.UINT,
        ]

        gdi32.GetDIBits.restype = ctypes.c_int

        lines = gdi32.GetDIBits(
            hdc,
            bitmap,
            0,
            height,
            buffer,
            ctypes.byref(bitmap_info),
            0
        )

        if lines != height:
            return None

        return QImage(
            buffer,
            width,
            height,
            stride,
            QImage.Format.Format_ARGB32
        ).copy()

    finally:
        user32.ReleaseDC(None, hdc)


# ---------------------------------------------------------
# Главная функция получения миниатюры
# ---------------------------------------------------------

def get_thumbnail(path, width=160, height=160):
    """
    Получает миниатюру фотографии через Windows Shell.

    Сначала проверяет системный кэш.
    Если готовой миниатюры нет, Windows создаёт её.

    Возвращает QImage или None при ошибке.
    """

    if not os.path.isfile(path):
        return None

    initialized = False

    ole32.CoInitializeEx.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD
    ]
    ole32.CoInitializeEx.restype = ctypes.c_long

    ole32.CoUninitialize.restype = None

    # Инициализируем COM в текущем потоке.
    com_result = ole32.CoInitializeEx(None, 0x2)

    if com_result >= 0:
        initialized = True
    elif com_result != -2147417850:
        return None

    interface = None
    bitmap = None

    try:
        interface = _get_shell_image_factory(
            os.path.abspath(path)
        )

        if interface is None:
            return None

        # Сначала пробуем получить готовую миниатюру из кэша.
        bitmap = _get_hbitmap(
            interface,
            width,
            height,
            SIIGBF_INCACHEONLY
        )

        # Если её нет, разрешаем Windows подготовить изображение.
        if bitmap is None:
            bitmap = _get_hbitmap(
                interface,
                width,
                height,
                SIIGBF_RESIZETOFIT
            )

        if bitmap is None:
            return None

        return _bitmap_to_qimage(bitmap)

    finally:
        if bitmap is not None and bitmap.value:
            gdi32.DeleteObject(bitmap)

        if interface is not None:
            _release_interface(interface)

        if initialized:
            ole32.CoUninitialize()
