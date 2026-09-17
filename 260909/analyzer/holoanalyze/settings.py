"""Editable optical and CPU parameters; camera acquisition nodes remain read-only."""
from dataclasses import replace
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QDialogButtonBox,
    QSpinBox, QLabel, QLineEdit, QTabWidget, QWidget)
from .widgets import number, tip


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("光学・計算設定")
        self.setMinimumWidth(510)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        self.fields = {}
        optical = QWidget()
        form = QFormLayout(optical)
        self.add_float(form, "波長", "wavelength_nm", .01, 100000, " nm", "光源の波長です。光学条件の変更後は焦点探索とキャリブレーションをやり直します。")
        self.add_float(form, "画素ピッチ", "pixel_pitch_um", .0001, 100000, " µm", "センサーの画素ピッチです。暗黙の画像縮小は行いません。")
        self.add_int(form, "パディング辺長", "padding_size", 16, 8192, "表示・GS用の正方形配列の辺長です。Tamura探索は元画像サイズでパディングしません。既定4096。入力場を中央に置き、周囲は場の平均値で埋めます。入力画像より小さい値では計算を拒否します。")
        self.add_int(form, "CPU計算スレッド", "compute_threads", 1, 4, "FFTに使用するCPUスレッド数です。既定4、最大4。画面とカメラ取得は別スレッドです。GPUは使用しません。")
        explanation = QLabel("GSの往復伝搬：常に帯域制限なし\n再生像の帯域制限あり／なしは、各画像のチェックで切替\n光学条件を変更すると、解析結果と適用済み補正は無効になります。")
        explanation.setWordWrap(True)
        form.addRow(explanation)
        tabs.addTab(optical, "光学・CPU")
        focus = QWidget()
        form = QFormLayout(focus)
        self.add_float(form, "ピークの最小突出率", "peak_prominence_fraction", 0, 1, "", "ピークの突出量を、そのピーク値に対する比率で指定します。既定0.005（0.5%）。探索端は常に除外し、両側に下りがある山のみ候補にします。")
        self.add_float(form, "ピークの最小幅", "peak_min_width_samples", 1, 1000, " 点", "半突出量で測ったピーク幅の下限です。深度のサンプル数を単位に指定します。")
        form.addRow(QLabel("Tamura探索：パディングなし、曲線のみ保持\n深度変更時：平均値パディングでその都度再生"))
        tabs.addTab(focus, "焦点探索")
        calibration = QWidget()
        form = QFormLayout(calibration)
        for label, key, lo, hi, text in (
            ("相関窓サイズ", "calibration_window_px", 16, 512, "ガラスプレートの局所対応を求める正方形窓の幅です。"),
            ("対応点の間隔", "calibration_step_px", 8, 512, "画像内に置く局所相関点の間隔です。"),
            ("局所探索半径", "calibration_search_px", 2, 512, "全体の並進位置を推定した後に行う局所探索の半径です。"),
            ("最小対応点数", "calibration_min_matches", 12, 10000, "画像変換を採用するために必要な対応点数です。点は画像内に分布している必要があります。")):
            self.add_int(form, label, key, lo, hi, text)
        self.add_float(form, "フィットRMS上限", "calibration_max_rms_px", .001, 100, " px", "二次変換のフィット残差の上限です。超過する場合は適用できません。")
        self.add_float(form, "検証RMS上限", "calibration_max_holdout_px", .001, 100, " px", "保留点の検証および補正後の対応点残差の上限です。")
        tabs.addTab(calibration, "画像変換")
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("設定を適用")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        tip(buttons.button(QDialogButtonBox.StandardButton.Ok), "入力値を検証して設定を更新します。YAMLへの保存はメイン画面の「設定を保存」で行えます。")
        tip(buttons.button(QDialogButtonBox.StandardButton.Cancel), "設定を変更せず閉じます。")
        buttons.accepted.connect(self.accept_validated)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_float(self, form, label, key, low, high, suffix, explanation):
        control = number(getattr(self.config, key), low, high, decimals=5, suffix=suffix)
        tip(control, explanation)
        self.fields[key] = control
        form.addRow(label, control)

    def add_int(self, form, label, key, low, high, explanation):
        control = QSpinBox()
        control.setRange(low, high)
        control.setValue(getattr(self.config, key))
        control.setKeyboardTracking(False)
        tip(control, explanation)
        self.fields[key] = control
        form.addRow(label, control)

    def accept_validated(self):
        try:
            changes = {key: (control.text().strip() or None) if isinstance(control, QLineEdit) else control.value()
                       for key, control in self.fields.items()}
            self.result_config = replace(self.config, **changes).validate()
            self.accept()
        except Exception as exc:
            self.error.setText(str(exc))
