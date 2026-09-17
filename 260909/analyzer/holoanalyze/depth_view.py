"""A cached depth browser shared by acquisition and the two focus calibrations."""
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QComboBox, QSlider)
from .widgets import ImagePanel, FocusPlot, number, tip


class DepthViewer(QWidget):
    depthRequested = Signal(float)
    depthChanged = Signal(float)

    def __init__(self, title, layout_mode="below"):
        super().__init__()
        self.analysis = self.rendered = None
        self.pending_depth = None
        self.normalize = True
        self.base_title = title
        layout = QHBoxLayout(self) if layout_mode == "beside" else QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.panel = ImagePanel(title)
        layout.addWidget(self.panel, 1)
        self.details = QWidget()
        details = QVBoxLayout(self.details)
        details.setContentsMargins(2, 2, 2, 2)
        details.setSpacing(6)
        if layout_mode != "external":
            layout.addWidget(self.details, 1 if layout_mode == "beside" else 0)
        if layout_mode == "beside":
            self.panel.setMaximumWidth(430)
        row = QHBoxLayout()
        row.addWidget(QLabel("深度"))
        self.depth = number(0)
        self.depth.setMinimumWidth(128)
        self.depth.editingFinished.connect(lambda: self.select_depth(self.depth.value()))
        tip(self.depth, "任意の深度を入力してEnterで再生します。計算済みの深度は保存した画像を直ちに表示し、未計算の深度だけバックグラウンドで計算します。")
        row.addWidget(self.depth)
        self.filtered = QCheckBox("帯域制限あり")
        self.filtered.setChecked(True)
        tip(self.filtered, "同じ深度で計算済みの「帯域制限あり／なし」を切り替えます。GSの往復伝搬は常に帯域制限なしです。")
        self.filtered.toggled.connect(self.filter_changed)
        if layout_mode != "external":
            row.addWidget(self.filtered)
        row.addStretch()
        details.addLayout(row)
        if layout_mode == "external":
            details.addWidget(self.filtered)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        tip(self.slider, "探索で計算した深度を移動します。全深度の再生画像を保持しているため、FFTの再計算は行いません。")
        self.slider.valueChanged.connect(self.slider_changed)
        details.addWidget(self.slider)
        self.plot = FocusPlot()
        self.plot.setMinimumHeight(125 if layout_mode == "beside" else 170)
        self.plot.setMaximumHeight(210)
        self.plot.depthSelected.connect(self.select_depth)
        details.addWidget(self.plot, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("山型ピーク"))
        self.peaks = QComboBox()
        self.peaks.setMinimumContentsLength(12)
        self.peaks.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        tip(self.peaks, "両側に下りがあるピークの候補です。探索端の最大値は採用しません。山の突出量が大きい順に並びます。")
        self.peaks.activated.connect(self.peak_selected)
        row.addWidget(self.peaks, 1)
        details.addLayout(row)
        self.status = QLabel("Analyzeで深度を探索してください。")
        self.status.setWordWrap(True)
        details.addWidget(self.status)
        self.refresh_enabled()

    def clear(self):
        self.pending_depth = None
        self.panel.view.clear()
        self.rendered = self.analysis = None
        self.plot.rows, self.plot.peaks, self.plot.depth = [], [], None
        self.plot.update()
        self.panel.title.setText(self.base_title)
        self.peaks.clear()
        self.status.setText("Analyzeで深度を探索してください。")
        self.refresh_enabled()

    def set_analysis(self, result):
        self.analysis = result
        self.plot.rows = list(result.curve)
        self.slider.setRange(0, max(0, len(result.curve)-1))
        self.update_peaks()
        best = result.best_filtered if self.filtered.isChecked() else result.best_unfiltered
        if best is not None:
            self.select_depth(best.z_mm)
        elif result.curve:
            # Show a neutral middle sample without claiming it is a focus.
            self.select_depth(result.curve[len(result.curve)//2][0])
        self.refresh_enabled()

    def refresh_enabled(self):
        ready = self.analysis is not None
        self.depth.setEnabled(ready)
        self.slider.setEnabled(ready and bool(self.analysis.curve))
        self.filtered.setEnabled(ready)
        self.peaks.setEnabled(ready and self.peaks.count() > 0)

    def update_peaks(self):
        if self.analysis is None:
            return
        filtered = self.filtered.isChecked()
        candidates = self.analysis.peaks_filtered if filtered else self.analysis.peaks_unfiltered
        self.plot.peaks = candidates
        self.plot.peak_column = 1 if filtered else 2
        self.peaks.clear()
        for index in candidates:
            z = self.analysis.curve[index][0]
            self.peaks.addItem(f"{z:.4f} mm", z)
        if not candidates:
            self.status.setText("山型ピークなし：範囲を広げるか、曲線と画像を見て焦点を手動指定してください。")
        else:
            suffix = "（中断した範囲の結果）" if self.analysis.stopped else ""
            cache = self.analysis.reconstruction.cache
            self.status.setText(f"{len(self.analysis.curve)} 深度を保持 · {len(candidates)} ピーク {suffix}")
            tip(self.status, f"メモリ保持 {len(cache)-cache.disk_count} 深度、一時ファイル保持 {cache.disk_count} 深度。Resume・再解析・終了時にこのキャッシュを解放します。")
        self.peaks.setEnabled(bool(candidates))
        self.plot.update()

    def slider_changed(self, index):
        if self.analysis and 0 <= index < len(self.analysis.curve):
            self.select_depth(self.analysis.curve[index][0])

    def peak_selected(self, index):
        value = self.peaks.itemData(index)
        if value is not None:
            self.select_depth(value)

    def select_depth(self, z):
        if self.analysis is None:
            return
        z = float(z)
        self.depth.blockSignals(True)
        self.depth.setValue(z)
        self.depth.blockSignals(False)
        if self.analysis.curve:
            index = int(np.argmin(abs(np.asarray(self.analysis.curve)[:, 0]-z)))
            self.slider.blockSignals(True)
            self.slider.setValue(index)
            self.slider.blockSignals(False)
        cached = self.analysis.reconstruction.cached(z)
        if cached is not None:
            self.pending_depth = None
            self.show_render(cached)
        else:
            self.pending_depth = z
            self.panel.title.setText(f"{self.base_title} · z = {z:.4f} mm を計算待ち")
            self.depthRequested.emit(z)

    def show_render(self, render):
        self.rendered = render
        self.panel.view.set_pixels(render.pixels(self.filtered.isChecked(), self.normalize))
        self.panel.title.setText(f"{self.base_title} · z = {render.z_mm:.4f} mm")
        self.panel.title.setToolTip(self.panel.title.text())
        self.plot.depth = render.z_mm
        self.plot.update()
        self.depthChanged.emit(render.z_mm)

    def filter_changed(self):
        self.update_peaks()
        if self.rendered is not None:
            self.show_render(self.rendered)

    def accept_render(self, render):
        if self.pending_depth == render.z_mm:
            self.pending_depth = None
            self.show_render(render)

    def toggle_contrast(self):
        self.normalize = not self.normalize
        if self.rendered is not None:
            self.show_render(self.rendered)
