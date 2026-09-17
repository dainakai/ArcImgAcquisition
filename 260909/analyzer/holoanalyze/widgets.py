"""Plain Qt image panels, clickable focus curves and vector-map diagnostics."""
import html
import math
import numpy as np
from PySide6.QtCore import QPointF, Qt, QRectF, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QGraphicsScene, QGraphicsView, QWidget, QLabel, QVBoxLayout,
    QHBoxLayout, QPushButton, QDoubleSpinBox, QPlainTextEdit, QSizePolicy)
from .cache import display_pixels


def tip(widget, text):
    widget.setToolTip('<p style="white-space:pre-wrap">'+html.escape(text)+'</p>')
    widget.setAccessibleDescription(text)
    return widget


def button(text, callback, explanation):
    result = QPushButton(text)
    result.setMinimumHeight(34)
    tip(result, explanation)
    if callback is not None:
        result.clicked.connect(callback)
    return result


def number(value, low=-100000, high=100000, decimals=4, suffix=" mm"):
    result = QDoubleSpinBox()
    result.setDecimals(decimals)
    result.setRange(low, high)
    result.setValue(value)
    result.setKeyboardTracking(False)
    result.setSuffix(suffix)
    tip(result, "数値を入力してEnterで確定します。上下ボタンでも調整できます。")
    return result


def preview_pixels(image, normalize=True, limits=None):
    a = np.asarray(image)
    if limits is None:
        if normalize:
            stride = max(1, int(math.sqrt(a.size/300000)))
            low, high = np.percentile(a[::stride, ::stride], [1, 99])
        else:
            low = 0
            high = np.iinfo(a.dtype).max if a.dtype.kind in "ui" else float(a.max())
    else:
        low, high = limits
    return display_pixels(a.astype(np.float32), (low, high))


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
        self.setBackgroundBrush(QColor("#e9edf1"))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setMinimumSize(140, 100)
        self.fitted = True
        self.pixels = None
        tip(self, "ホイールで拡大・縮小、ドラッグで移動できます。スクロールバーで縦横に移動できます。画像の縦横比は保持します。")

    def set_pixels(self, pixels):
        self.pixels = pixels
        self.item.setPixmap(QPixmap.fromImage(qimage(pixels)))
        self.scene().setSceneRect(self.item.boundingRect())
        if self.fitted:
            self.fit_image()

    def set_image(self, image, normalize=True, limits=None, live=False):
        stride = max(1, int(np.ceil(max(image.shape)/1000))) if live else 1
        self.set_pixels(preview_pixels(image[::stride, ::stride], normalize, limits))

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
        if .015 <= self.transform().m11()*factor <= 64:
            self.fitted = False
            self.scale(factor, factor)

    def wheelEvent(self, event):
        self.zoom(1.2 if event.angleDelta().y() > 0 else 1/1.2)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fitted:
            self.fit_image()


class ImagePanel(QWidget):
    def __init__(self, title, source=False):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.title, 1)
        self.view = ImageView()
        for label, callback, help_text in (
            ("−", lambda: self.view.zoom(1/1.25), "画像を縮小します。"),
            ("＋", lambda: self.view.zoom(1.25), "画像を拡大します。"),
            ("全体", self.view.fit_image, "画像全体が入る倍率に合わせます。"),
            ("1:1", self.view.original_size, "画像の1画素を表示の1画素に合わせます。")):
            if source and label in ("−", "＋"):
                continue  # The narrow camera column uses wheel zoom.
            control = button(label, callback, help_text)
            control.setFixedWidth(42 if len(label) > 1 else 32)
            row.addWidget(control)
        layout.addLayout(row)
        layout.addWidget(self.view, 1)
        self.source = QPlainTextEdit()
        self.source.setReadOnly(True)
        self.source.setMaximumHeight(49)
        self.source.setMinimumHeight(42)
        self.source.setPlaceholderText("入力画像のパス")
        tip(self.source, "読み込んだ画像のフルパスです。文字を選択してコピーできます。長いパスはスクロールして確認できます。")
        if source:
            layout.addWidget(self.source)
        else:
            self.source.hide()

    def show_frame(self, frame, origin="files", normalize=True, live=False):
        if frame is None:
            self.view.clear()
            self.source.setPlainText("画像がありません")
            return
        self.view.set_image(frame.image, normalize, live=live)
        h, w = frame.image.shape
        label = f"{frame.serial} · {w}×{h} · #{frame.frame_id}"
        self.title.setText(label)
        self.title.setToolTip(label)
        path = frame.path or ("ライブ取得・未保存" if live else f"Capture・未保存（{origin}）")
        if self.source.toPlainText() != path:
            self.source.setPlainText(path)
        tip(self.source, path+"\n文字を選択してコピーできます。")


class FocusPlot(QWidget):
    depthSelected = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows, self.peaks = [], []
        self.peak_column = 1
        self.depth = None
        self.setMinimumHeight(170)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        tip(self, "曲線上をクリックすると、その位置に最も近い計算済み深度へ移動します。丸印は探索端を除いた山型ピークです。")

    def plot_rect(self):
        return QRectF(62, 28, max(10, self.width()-80), max(10, self.height()-67))

    def select_at(self, position):
        if not self.rows or not self.plot_rect().contains(position):
            return None
        data = np.asarray(self.rows)
        low, high = data[:, 0].min(), data[:, 0].max()
        z = low+(position.x()-self.plot_rect().left())/self.plot_rect().width()*(high-low)
        return int(np.argmin(abs(data[:, 0]-z)))

    def mousePressEvent(self, event):
        index = self.select_at(event.position())
        if event.button() == Qt.MouseButton.LeftButton and index is not None:
            self.depthSelected.emit(float(self.rows[index][0]))
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        index = self.select_at(event.position())
        if index is not None:
            z, f, u = self.rows[index]
            self.setToolTip(f"z = {z:.4f} mm\n帯域制限あり: {f:.6g}\nなし: {u:.6g}\nクリックでこの深度を表示")
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#f8fafc"))
        plot = self.plot_rect()
        p.setPen(QColor("#465464"))
        p.drawText(10, 18, "Tamura  σ(I) / mean(I)" if self.width() > 440 else "Tamura")
        if self.width() > 440:
            p.setPen(QColor("#247e96")); p.drawText(self.width()-180, 18, "● 制限あり")
            p.setPen(QColor("#a87532")); p.drawText(self.width()-88, 18, "● なし")
        else:
            p.setPen(QColor("#247e96")); p.drawText(self.width()-100, 18, "● 有")
            p.setPen(QColor("#a87532")); p.drawText(self.width()-49, 18, "● 無")
        p.setPen(QColor("#bdc7d2")); p.drawRect(plot)
        if not self.rows:
            p.setPen(QColor("#687686"))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, "深度探索の結果を表示します")
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
        for col, color in ((1, "#247e96"), (2, "#a87532")):
            p.setPen(QPen(QColor(color), 1.8))
            path = QPainterPath()
            for i, row in enumerate(data):
                pt = point(row[0], row[col])
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            p.drawPath(path)
        p.setPen(QPen(QColor("#247e96"), 2))
        for i in self.peaks:
            if 0 <= i < len(data):
                p.drawEllipse(point(data[i, 0], data[i, self.peak_column]), 4, 4)
        p.setPen(QColor("#586575"))
        for v in (ymin, ymax):
            p.drawText(QRectF(0, point(xmin, v).y()-8, 56, 20), Qt.AlignmentFlag.AlignRight, f"{v:.3g}")
        p.drawText(int(plot.left()), int(plot.bottom()+20), f"{xmin:.3f}")
        p.drawText(int(plot.right()-48), int(plot.bottom()+20), f"{xmax:.3f}")
        p.drawText(int(plot.center().x()-30), self.height()-3, "深度 [mm]")
        if self.depth is not None and xmin <= self.depth <= xmax:
            p.setPen(QPen(QColor("#555"), 1, Qt.PenStyle.DashLine))
            p.drawLine(point(self.depth, ymin), point(self.depth, ymax))


class VectorPlot(QWidget):
    def __init__(self, title):
        super().__init__()
        self.title, self.vectors, self.shape = title, [], (1, 1)
        self.setMinimumSize(240, 200)
        tip(self, "各矢印は対応点の変位です。補正後は残差を示します。矢印は見やすい倍率で表示し、図中の倍率・スケールで実画素量を確認できます。")

    def set_vectors(self, vectors, shape):
        self.vectors, self.shape = vectors, shape
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#f8fafc"))
        p.setPen(QColor("#465464")); p.drawText(12, 22, self.title)
        if not len(self.vectors):
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "ベクトルマップ計算後に表示します")
            return
        data = np.asarray(self.vectors)
        h, w = self.shape
        scale = min((self.width()-40)/w, (self.height()-100)/h)
        origin = QPointF((self.width()-w*scale)/2, 40)
        magnitude = np.linalg.norm(data[:, 2:4], axis=1)
        gain = min(100, max(1, 20/max(float(np.percentile(magnitude, 90))*scale, .1)))
        p.setPen(QPen(QColor("#c6cfd8"), 1))
        p.drawRect(QRectF(origin, QPointF(origin.x()+w*scale, origin.y()+h*scale)))
        p.setPen(QPen(QColor("#247e96"), 1.3))
        stride = max(1, int(np.ceil(len(data)/500)))
        for x, y, dx, dy in data[::stride]:
            start = origin+QPointF(x*scale, y*scale)
            end = start+QPointF(dx*scale*gain, dy*scale*gain)
            p.drawLine(start, end)
            angle = math.atan2(dy, dx)
            for offset in (-.55, .55):
                p.drawLine(end, end-QPointF(4*math.cos(angle+offset), 4*math.sin(angle+offset)))
        rms = float(np.sqrt(np.mean(magnitude**2)))
        p.setPen(QColor("#465464"))
        p.drawText(12, self.height()-32, f"{len(data)} 点 · RMS {rms:.3f} px · 矢印倍率 {gain:.1f}×")
        unit = max(.1, round(35/max(scale*gain, .01), 1))
        length = unit*scale*gain
        p.drawLine(QPointF(15, self.height()-14), QPointF(15+length, self.height()-14))
        p.drawText(int(22+length), self.height()-9, f"{unit:g} px")
