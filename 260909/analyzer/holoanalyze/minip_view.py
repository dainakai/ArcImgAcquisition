"""MinIP controls and image viewer, separate from the Tamura depth browser."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QLabel, QComboBox, QSpinBox, QCheckBox
from .cache import padding_label
from .widgets import ImagePanel, button, number, tip

MODE_LABELS = {"gabor_cam0": "cam0 Gabor", "gabor_cam1": "cam1 Gabor", "phase": "位相回復"}


class MinIPViewer(QWidget):
    requested = Signal()

    def __init__(self, config):
        super().__init__()
        self.result = self.metadata = None
        self.normalize = True
        self.controls = QWidget()
        layout = QVBoxLayout(self.controls)
        layout.setContentsMargins(6, 10, 6, 6)
        note = QLabel("範囲と間隔を指定してMinIPを作成します。深度探索の実行は不要です。")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.mode = QComboBox()
        for label, mode in (("Gabor · cam0", "gabor_cam0"), ("Gabor · cam1", "gabor_cam1"), ("PR · 位相回復", "phase")):
            self.mode.addItem(label, mode)
        tip(self.mode, "Gaborは各カメラから再構成します。PRは適用済みキャリブレーションでGSを一度実行し、cam0面を基準に各深度を再構成します。")
        form.addRow("モード", self.mode)
        lo, hi = config.scan_bounds(0)
        self.minimum, self.maximum = number(lo), number(hi)
        self.step = number(config.scan_step_mm, .0001, 100000)
        form.addRow("最小深度", self.minimum)
        form.addRow("最大深度", self.maximum)
        form.addRow("再構成間隔", self.step)
        tip(self.minimum, "選択したカメラ面からの符号付き伝搬距離です。PRはcam0面を基準にします。")
        tip(self.maximum, "最小深度から間隔ずつ進み、この値以下の深度を計算します。端数の最終間隔は追加しません。")
        tip(self.step, "深度方向の再構成間隔です。各深度のfloat強度から最小値を逐次集約し、完成後に表示用の8 bitへ変換します。最大10001深度です。")
        self.iterations = QSpinBox()
        self.iterations.setRange(1, 10000)
        self.iterations.setValue(config.gs_iterations)
        tip(self.iterations, "PRのGS反復回数です。MinIP作成時に一度だけ実行し、全深度で同じ位相場を使います。")
        form.addRow("GS反復回数", self.iterations)
        self.padding = QComboBox()
        for label, size in (("なし", 0), ("4k · 4096", 4096), ("8k · 8192", 8192)):
            self.padding.addItem(label, size)
        self.padding.setCurrentIndex(self.padding.findData(config.display_padding_size))
        tip(self.padding, "MinIP再構成のパディングです。4k／8kは場の平均値で埋めます。GSのパディングは光学設定に従います。")
        form.addRow("パディング", self.padding)
        layout.addLayout(form)
        self.create_button = button("MinIPを作成", self.requested.emit, "指定範囲の最小強度投影をバックグラウンドで計算します。Q / Escで中断できます。中断した投影は採用しません。")
        layout.addWidget(self.create_button)
        self.filtered = QCheckBox("帯域制限あり")
        self.filtered.setChecked(True)
        tip(self.filtered, "完成したMinIPの帯域制限あり／なしを切り替えます。どちらも深度ごとの2次元帯域条件で計算済みです。GS往復は帯域制限なしです。")
        self.filtered.toggled.connect(self.refresh_image)
        layout.addWidget(self.filtered)
        self.status = QLabel("MinIPは未計算です。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        view_layout = QVBoxLayout(self)
        view_layout.setContentsMargins(0, 0, 0, 0)
        self.panel = ImagePanel("MinIP")
        view_layout.addWidget(self.panel, 1)
        self.explanation = QLabel("MinIP（最小強度投影）は、再構成体積の各画素 (x, y) について、指定した奥行き方向の強度の最小値を取った画像です。")
        self.explanation.setWordWrap(True)
        view_layout.addWidget(self.explanation)

    def clear(self):
        self.result = self.metadata = None
        self.panel.view.clear()
        self.panel.title.setText("MinIP")
        self.status.setText("MinIPは未計算です。")

    def accept_result(self, result, metadata):
        self.result, self.metadata = result, metadata
        self.refresh_image()

    def refresh_image(self):
        if self.result is None:
            return
        result, metadata = self.result, self.metadata
        self.panel.view.set_pixels(result.pixels(self.filtered.isChecked(), self.normalize))
        title = f"MinIP · {MODE_LABELS[metadata['mode']]} · {result.depths_mm[0]:g}〜{result.depths_mm[-1]:g} mm"
        self.panel.title.setText(title)
        tip(self.panel.title, title)
        self.status.setText(f"計算済み：{MODE_LABELS[metadata['mode']]}\n"
            f"{result.depths_mm[0]:g}〜{result.depths_mm[-1]:g} mm / {metadata['step_mm']:g} mm間隔\n"
            f"{len(result.depths_mm)} 深度 · {padding_label(result.padding_size)}")

    def toggle_contrast(self):
        self.normalize = not self.normalize
        self.refresh_image()
