import os

from PySide6.QtWidgets import (
    QListWidget,
    QAbstractItemView,
)

from PySide6.QtGui import QDrag

from PySide6.QtCore import (
    Qt,
    QSize,
    QEvent,
)


# Размер миниатюр фотографий в сетке.
THUMBNAIL_SIZE = 60

class PhotoGrid(QListWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        # Разрешаем перетаскивать фотографии из сетки.
        self.setDragEnabled(True)

        # Разрешаем принимать файлы из Проводника Windows.
        self.setAcceptDrops(True)

        # Разрешаем перетаскивание между виджетами.
        self.setDragDropMode(
            QAbstractItemView.DragDropMode.DragDrop
        )

        # Показываем индикатор места вставки.
        self.setDropIndicatorShown(True)

        # По умолчанию при приёме файлов используем копирование.
        self.setDefaultDropAction(
            Qt.DropAction.CopyAction
        )

        # Внутренняя область QListWidget принимает файлы из Проводника.
        self.viewport().setAcceptDrops(True)
        self.viewport().installEventFilter(self)
               

    # -----------------------------------------------------
    # ОБРАБОТКА НАЖАТИЯ И ДВИЖЕНИЯ МЫШИ
    # -----------------------------------------------------

    def mousePressEvent(self, event):
        # Запоминаем позицию нажатия левой кнопки.
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_position = event.position().toPoint()

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Проверяем, удерживается ли левая кнопка.
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return super().mouseMoveEvent(event)

        # Проверяем, выделена ли фотография.
        if not self.selectedItems():
            return super().mouseMoveEvent(event)

        # Проверяем, достаточно ли далеко переместилась мышь.
        distance = (
            event.position().toPoint()
            - self.drag_start_position
        ).manhattanLength()

        if distance < 10:
            return

        # Запускаем перетаскивание.
        self.startDrag(Qt.DropAction.MoveAction)

    def mimeData(self, items):
        from PySide6.QtCore import QMimeData, QUrl

        mime_data = QMimeData()
        urls = []

        for item in items:
            path = item.data(Qt.ItemDataRole.UserRole)

            if not path or not os.path.isfile(path):
                continue

            urls.append(
                QUrl.fromLocalFile(os.path.abspath(path))
            )

        mime_data.setUrls(urls)

        return mime_data

    # -----------------------------------------------------
    # РУЧНОЙ ЗАПУСК ПЕРЕТАСКИВАНИЯ ФОТОГРАФИЙ
    # -----------------------------------------------------

    def startDrag(self, supported_actions):
        # Получаем выделенные фотографии.
        selected_items = self.selectedItems()

        if not selected_items:
            return

        # Формируем данные с реальными путями файлов.
        mime_data = self.mimeData(selected_items)

        if not mime_data.hasUrls():
            return

        # Создаём объект перетаскивания.
        drag = QDrag(self)
        drag.setMimeData(mime_data)

        # Показываем миниатюру перетаскиваемой фотографии.
        selected_item = selected_items[0]

        if not selected_item.icon().isNull():
            drag.setPixmap(
                selected_item.icon().pixmap(
                    QSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE)
                )
            )

        # Разрешаем системе выбрать подходящее действие.
        allowed_actions = (
            Qt.DropAction.CopyAction
            | Qt.DropAction.MoveAction
        )

        drag.exec(allowed_actions)

    # -----------------------------------------------------
    # ПРИЁМ ФАЙЛОВ ЧЕРЕЗ ВНУТРЕННЮЮ ОБЛАСТЬ QListWidget
    # -----------------------------------------------------

    def eventFilter(self, watched, event):

        if watched is self.viewport():
            if event.type() in (
                QEvent.Type.DragEnter,
                QEvent.Type.DragMove,
                QEvent.Type.Drop
            ):
                print("VIEWPORT EVENT:", event.type())
        # Обрабатываем перетаскивание только во внутренней области.
        if watched is self.viewport():

            # Начало перетаскивания.
            if event.type() == QEvent.Type.DragEnter:
                if event.mimeData().hasUrls():
                    event.setDropAction(Qt.DropAction.CopyAction)
                    event.accept()
                    return True

            # Движение файла над сеткой.
            elif event.type() == QEvent.Type.DragMove:
                if event.mimeData().hasUrls():
                    event.setDropAction(Qt.DropAction.CopyAction)
                    event.accept()
                    return True

            # Отпускание файла над сеткой.
            elif event.type() == QEvent.Type.Drop:
                if event.mimeData().hasUrls():
                    paths = []

                    for url in event.mimeData().urls():
                        if url.isLocalFile():
                            paths.append(url.toLocalFile())

                    callback = getattr(self, "drop_callback", None)

                    if paths and callback:
                        callback(paths)
                        event.setDropAction(Qt.DropAction.CopyAction)
                        event.accept()
                        return True

        return super().eventFilter(watched, event)

    # -----------------------------------------------------
    # ПРИЁМ ФОТОГРАФИЙ ИЗ ПРОВОДНИКА WINDOWS
    # -----------------------------------------------------

    # -----------------------------------------------------
    # ПРИЁМ ФОТОГРАФИЙ ИЗ ПРОВОДНИКА WINDOWS
    # -----------------------------------------------------

    def dragEnterEvent(self, event):
        # Разрешаем перетаскивание файлов в главное окно.
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        # Проверяем, находится ли курсор над сеткой фотографий.
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
        # Получаем файлы, только если отпускание произошло над сеткой.
        position = self.files_list.viewport().mapFrom(
            self,
            event.position().toPoint()
        )

        if not self.files_list.viewport().rect().contains(position):
            event.ignore()
            return

        paths = []

        for url in event.mimeData().urls():
            if url.isLocalFile():
                paths.append(url.toLocalFile())

        if paths:
            self.copy_dropped_photos(paths)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        # Разрешаем перемещение курсора над сеткой.
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.CopyAction)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        # Получаем пути перетаскиваемых файлов.
        if not event.mimeData().hasUrls():
            event.ignore()
            return

        paths = []

        for url in event.mimeData().urls():
            if url.isLocalFile():
                paths.append(url.toLocalFile())

        if not paths:
            event.ignore()
            return

        # Передаём файлы главному окну для копирования.
        callback = getattr(self, "drop_callback", None)

        if callback:
            callback(paths)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()