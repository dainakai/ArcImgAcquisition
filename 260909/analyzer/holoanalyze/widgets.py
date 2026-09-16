"""Native Qt image pan/zoom and a lightweight incremental focus plot."""
import numpy as np
from PySide6.QtCore import QPointF, Qt, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QWidget


def preview_pixels(image, normalize=True, limits=None):
    a = np.asarray(image)
    if limits is None:
        if normalize:
            low, high = np.percentile(a, [1, 99])
        else:
            low = 0
            high = np.iinfo(a.dtype).max if a.dtype.kind in "ui" else float(a.max())
    else:
        low, high = limits
    return np.rint(np.clip((a.astype(np.float32)-low)/max(float(high-low), 1e-12), 0, 1)*255).astype(np.uint8)


def qimage(pixels):
    a = np.ascontiguousarray(pixels)
    return QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format.Format_Grayscale8).copy()


class ImageView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.item = self.scene().addPixmap(QPixmap())
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#151b23"))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setMinimumSize(160, 100)
        self.fitted = True
        self.pixels = None

    def set_image(self, image, normalize=True, limits=None):
        self.pixels = preview_pixels(image, normalize, limits)
        self.item.setPixmap(QPixmap.fromImage(qimage(self.pixels)))
        self.scene().setSceneRect(self.item.boundingRect())
        if self.fitted:
            self.fit_image()

    def clear(self):
        self.item.setPixmap(QPixmap())
        self.pixels = None

    def fit_image(self):
        self.fitted = True
        if not self.item.pixmap().isNull():
            self.fitInView(self.item, Qt.AspectRatioMode.KeepAspectRatio)

    def original_size(self):
        self.fitted = False
        self.resetTransform()

    def zoom(self, factor):
        new_scale = self.transform().m11()*factor
        if .015 <= new_scale <= 64:
            self.fitted = False
            self.scale(factor, factor)

    def wheelEvent(self, event):
        self.zoom(1.2 if event.angleDelta().y() > 0 else 1/1.2)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fitted:
            self.fit_image()


class FocusPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.depth = None
        self.setMinimumHeight(160)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f7f9fc"))
        plot = QRectF(65, 27, max(10, self.width()-85), max(10, self.height()-65))
        painter.setPen(QColor("#586575"))
        painter.drawText(12, 18, "Tamura: std(I) / mean(I)")
        painter.setPen(QColor("#197c91"))
        painter.drawText(self.width()-285, 18, "● Filtered")
        painter.setPen(QColor("#b5723b"))
        painter.drawText(self.width()-200, 18, "● Unfiltered")
        painter.setPen(QColor("#586575"))
        painter.drawRect(plot)
        if not self.rows:
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "Analyze to scan depth")
            return
        data = np.asarray(self.rows)
        xmin, xmax = float(data[:, 0].min()), float(data[:, 0].max())
        if xmax == xmin:
            xmin, xmax = xmin-.5, xmax+.5
        ymin, ymax = float(data[:, 1:].min()), float(data[:, 1:].max())
        margin = max((ymax-ymin)*.08, .001)
        ymin, ymax = ymin-margin, ymax+margin

        def point(x, y):
            return QPointF(plot.left()+(x-xmin)/(xmax-xmin)*plot.width(),
                           plot.bottom()-(y-ymin)/(ymax-ymin)*plot.height())

        for col, color in ((1, "#197c91"), (2, "#b5723b")):
            painter.setPen(QPen(QColor(color), 1.8))
            path = QPainterPath()
            for i, row in enumerate(data):
                p = point(row[0], row[col])
                path.moveTo(p) if i == 0 else path.lineTo(p)
            painter.drawPath(path)
            if len(data) == 1:
                painter.drawEllipse(point(data[0, 0], data[0, col]), 3, 3)
        painter.setPen(QColor("#586575"))
        for v in (ymin, ymax):
            painter.drawText(QRectF(0, point(xmin, v).y()-8, 60, 20), Qt.AlignmentFlag.AlignRight, f"{v:.4f}")
        painter.drawText(int(plot.left()), int(plot.bottom()+20), f"{xmin:.3f}")
        painter.drawText(int(plot.right()-45), int(plot.bottom()+20), f"{xmax:.3f}")
        painter.drawText(int(plot.center().x()-35), self.height()-4, "Depth [mm]")
        if self.depth is not None and xmin <= self.depth <= xmax:
            painter.setPen(QPen(QColor("#555"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(point(self.depth, ymin), point(self.depth, ymax))
