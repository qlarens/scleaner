from pathlib import Path
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication

app = QApplication([])
image = QImage(256, 256, QImage.Format.Format_ARGB32)
image.fill(Qt.GlobalColor.transparent)
painter = QPainter(image)
painter.setRenderHint(QPainter.RenderHint.Antialiasing)
painter.setPen(Qt.PenStyle.NoPen)
painter.setBrush(QColor("#232039"))
painter.drawRoundedRect(QRectF(8, 8, 240, 240), 57, 57)
painter.setPen(QPen(QColor("#ae9fff"), 13, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
painter.setBrush(Qt.BrushStyle.NoBrush)
path = QPainterPath()
path.moveTo(121, 48)
for x, y in ((143, 103), (198, 125), (143, 147), (121, 202), (99, 147), (44, 125), (99, 103), (121, 48)):
    path.lineTo(x, y)
painter.drawPath(path)
painter.drawLine(192, 39, 192, 71)
painter.drawLine(176, 55, 208, 55)
painter.end()
destination = Path(__file__).resolve().parents[1] / "artifacts" / "SCleaner.ico"
destination.parent.mkdir(exist_ok=True)
if not image.save(str(destination)):
    raise RuntimeError("Could not write icon")
