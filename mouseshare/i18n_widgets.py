"""Qt widgets that accept Message objects directly and render them as text."""
from PySide6 import QtGui, QtWidgets

from .i18n import Message, render


class _Localized:
    def __init__(self, *args, **kwargs):
        super().__init__(*(render(arg) for arg in args), **kwargs)

    def setText(self, text):
        super().setText(render(text))

    def setToolTip(self, text):
        super().setToolTip(render(text))

    def setAccessibleName(self, text):
        super().setAccessibleName(render(text))

    def setAccessibleDescription(self, text):
        super().setAccessibleDescription(render(text))

    def setWindowTitle(self, text):
        super().setWindowTitle(render(text))

    def setPlaceholderText(self, text):
        super().setPlaceholderText(render(text))


# Explicitly imported by presentation modules; Qt's global classes remain untouched.
QLabel = type('QLabel', (_Localized, QtWidgets.QLabel), {})
QPushButton = type('QPushButton', (_Localized, QtWidgets.QPushButton), {})
QCheckBox = type('QCheckBox', (_Localized, QtWidgets.QCheckBox), {})
QAction = type('QAction', (_Localized, QtGui.QAction), {})
QWidget = type('QWidget', (_Localized, QtWidgets.QWidget), {})
QFrame = type('QFrame', (_Localized, QtWidgets.QFrame), {})
QDialog = type('QDialog', (_Localized, QtWidgets.QDialog), {})
QMainWindow = type('QMainWindow', (_Localized, QtWidgets.QMainWindow), {})
QLineEdit = type('QLineEdit', (_Localized, QtWidgets.QLineEdit), {})
QDoubleSpinBox = type('QDoubleSpinBox', (_Localized, QtWidgets.QDoubleSpinBox), {})
QGraphicsView = type('QGraphicsView', (_Localized, QtWidgets.QGraphicsView), {})
QSystemTrayIcon = type('QSystemTrayIcon', (_Localized, QtWidgets.QSystemTrayIcon), {})


class QComboBox(_Localized, QtWidgets.QComboBox):
    def addItem(self, text, userData=None):
        super().addItem(render(text), userData)


class QFormLayout(QtWidgets.QFormLayout):
    def addRow(self, *args):
        if args and isinstance(args[0], Message):
            label = QLabel(args[0])
            label.setWordWrap(True)
            return super().addRow(label, *args[1:])
        return super().addRow(*args)
