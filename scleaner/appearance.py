"""Accent settings and a small, previewable color dialog."""
from __future__ import annotations

import re

DEFAULT_ACCENT = "#9584ff"


def normalize_accent(value) -> str:
    return value.lower() if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) else DEFAULT_ACCENT


def accent_foreground(value: str) -> str:
    channels = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    luminance = sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))
    return "#141126" if luminance > .20 else "#ffffff"


def make_accent_dialog(current, parent=None):
    # Keep settings validation usable by the cleanup engine without loading Qt.
    from PySide6.QtCore import Qt, Signal
    from PySide6.QtGui import QColor, QPainter, QPen, QRegularExpressionValidator
    from PySide6.QtCore import QRegularExpression
    from PySide6.QtWidgets import QColorDialog, QDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget
    from .theme import stylesheet
    from .widgets import button, label

    class Preview(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setMinimumHeight(155)

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#1b1f2b"))
            painter.drawRoundedRect(self.rect(), 14, 14)
            painter.setPen(QPen(QColor("#303646"), 1.5))
            for x in range(12, self.width(), 8):
                for y in range(12, self.height(), 8):
                    painter.drawPoint(x, y)
            painter.setPen(QPen(QColor("#ffffff"), 4))
            painter.setBrush(QColor(dialog.color))
            painter.drawEllipse(self.rect().center(), 27, 27)

    class AccentDialog(QDialog):
        color_changed = Signal(str)

        def set_color(self, color):
            self.color = normalize_accent(color)
            self.setStyleSheet(stylesheet(self.color))
            self.hex_input.setText(self.color.upper())
            self.save_button.setEnabled(True)
            self.preview.update()
            for swatch_color, swatch in self.swatches:
                border = "#ffffff" if self.color == swatch_color else "#343b4e"
                swatch.setStyleSheet(f"QPushButton {{ background: {swatch_color}; border: 3px solid {border}; border-radius: 17px; padding: 0; }} QPushButton:focus {{ border-color: #ffffff; }}")
            self.color_changed.emit(self.color)

        def edit_hex(self, text):
            valid = bool(re.fullmatch(r"#[0-9a-fA-F]{6}", text))
            self.save_button.setEnabled(valid)
            if valid:
                self.set_color(text)

        def choose_custom(self):
            chosen = QColorDialog.getColor(QColor(self.color), self, "Свой цвет акцента", QColorDialog.ColorDialogOption.DontUseNativeDialog)
            if chosen.isValid():
                self.set_color(chosen.name())

    dialog = AccentDialog(parent)
    dialog.setWindowTitle("Цвет акцента")
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setFixedWidth(480)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(16)
    layout.addWidget(label("Ваш цвет SCleaner", "section"))
    layout.addWidget(label("Посмотрите, как меняется интерфейс, и сохраните понравившийся оттенок.", "muted", True))
    dialog.preview = Preview()
    layout.addWidget(dialog.preview)
    row = QHBoxLayout()
    dialog.swatches = []
    for name, color in [("Фиолетовый", DEFAULT_ACCENT), ("Кремовый", "#f3edda"), ("Розовый", "#efb3ce"), ("Сиреневый", "#e6ace2"), ("Коралловый", "#ef8a74"), ("Жёлтый", "#e2d17d"), ("Мятный", "#54dfb1"), ("Голубой", "#8ab8f5")]:
        swatch = button("", lambda checked=False, c=color: dialog.set_color(c))
        swatch.setFixedSize(34, 34)
        swatch.setAccessibleName(name)
        swatch.setToolTip(name + " · " + color.upper())
        row.addWidget(swatch)
        dialog.swatches.append((color, swatch))
    layout.addLayout(row)
    row = QHBoxLayout()
    row.addWidget(label("HEX", "muted"))
    dialog.hex_input = QLineEdit()
    dialog.hex_input.setAccessibleName("Цвет в формате HEX")
    dialog.hex_input.setMaxLength(7)
    dialog.hex_input.setValidator(QRegularExpressionValidator(QRegularExpression("#[0-9a-fA-F]{0,6}")))
    dialog.hex_input.textEdited.connect(dialog.edit_hex)
    row.addWidget(dialog.hex_input, 1)
    row.addWidget(button("Свой цвет…", dialog.choose_custom))
    layout.addLayout(row)
    dialog.reset_button = button("Сбросить к исходному фиолетовому", lambda: dialog.set_color(DEFAULT_ACCENT))
    layout.addWidget(dialog.reset_button)
    row = QHBoxLayout()
    row.addStretch()
    dialog.cancel_button = button("Отмена", dialog.reject)
    dialog.save_button = button("Сохранить цвет", dialog.accept, True)
    row.addWidget(dialog.cancel_button)
    row.addWidget(dialog.save_button)
    layout.addLayout(row)
    dialog.set_color(current)
    return dialog
