"""One camera controller and responsive CPU worker for two independent tabs."""
from dataclasses import replace
from pathlib import Path
import time
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTabWidget, QProgressBar, QFileDialog, QDialog, QMenu, QSizePolicy)
from .camera import NativeCamera, SimulatedCamera
from .config import load_config, save_config
from .data import Session
from .settings import SettingsDialog
from .widgets import button, tip
from .workers import Worker
from .workspace import AcquisitionWorkspace, CalibrationWorkspace


class MainWindow(QMainWindow):
    def __init__(self, config, config_path):
        super().__init__()
        self.config, self.config_path = config, Path(config_path)
        self.session = Session(config.output_dir)
        self.camera = self.live_pair = self.calibration = None
        self.last_live = 0
        self.worker = self.owner = None
        self.kind = ""
        self.closing = self.allow_close = False
        self.building = True
        self.setWindowTitle("DualHolo Analyze")
        self.resize(1480, 1000)
        self.setStyleSheet("""
            QWidget { font-size: 13px; }
            QPushButton { padding: 5px 9px; }
            QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { min-height: 29px; }
            QTabBar::tab { min-height: 29px; padding: 4px 18px; }
            QTabWidget::pane { border: 1px solid #c7cdd3; }
            QScrollArea { border: none; }
            QPlainTextEdit { font-size: 11px; border: 1px solid #d4d9df; background: #fafbfc; }
            QSplitter::handle { background: #dce1e6; }
            QSplitter::handle:horizontal { width: 5px; }
            QSplitter::handle:vertical { height: 5px; }
        """)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 10, 12, 8)
        layout.setSpacing(8)
        self.setCentralWidget(central)
        header = QHBoxLayout()
        title = QLabel("<b>DualHolo Analyze</b>")
        header.addWidget(title)
        self.state_label = QLabel("OFFLINE · カメラなしで使用できます")
        header.addWidget(self.state_label, 1)
        self.connect_button = button("カメラを接続", self.connect_camera,
            "既存のカメラ設定を読み取り、外部10 Hzトリガの画像を取得します。露光・ゲイン・ROI・反転・トリガなどの設定は変更しません。DualHolo等が実カメラを使用中は接続しないでください。")
        self.simulate_button = button("シミュレーション", self.start_simulation,
            "カメラなしで動く10 Hzの模擬画像を表示します。実カメラやSDKに接続しません。")
        self.settings_button = button("設定…", None, "光学・CPU設定を開くか、YAMLの読込・保存を選択します。")
        settings_menu = QMenu(self.settings_button)
        settings_menu.setToolTipsVisible(True)
        action = settings_menu.addAction("光学・CPU設定…", self.edit_settings)
        action.setToolTip("波長、画素ピッチ、パディング、スレッド数、ピークと画像変換の条件を変更します。")
        settings_menu.addSeparator()
        action = settings_menu.addAction("YAML設定を読込…", self.load_settings)
        action.setToolTip("保存したYAML設定を読み込みます。")
        action = settings_menu.addAction("YAML設定を保存…", self.save_settings)
        action.setToolTip("現在の設定をYAMLへ保存します。")
        self.settings_button.setMenu(settings_menu)
        for w in (self.connect_button, self.simulate_button, self.settings_button):
            header.addWidget(w)
        layout.addLayout(header)
        self.tabs = QTabWidget()
        self.acquisition = AcquisitionWorkspace(self)
        self.calibration_tab = CalibrationWorkspace(self)
        self.tabs.addTab(self.acquisition, "撮影・解析")
        self.tabs.addTab(self.calibration_tab, "キャリブレーション")
        self.tabs.setTabToolTip(0, "入力ペアを固定し、単画像Gabor再生またはキャリブレーション済み位相回復を行います。")
        self.tabs.setTabToolTip(1, "ガラスプレートから各カメラの焦点、面間距離、画像変換を求めて適用します。")
        self.tabs.currentChanged.connect(self.tab_changed)
        layout.addWidget(self.tabs, 1)
        progress_row = QHBoxLayout()
        self.progress_label = QLabel("待機中")
        self.progress_label.setMinimumWidth(180)
        self.progress_label.setMaximumWidth(440)
        self.progress_label.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setMaximumHeight(20)
        tip(self.progress, "現在の処理の進行状況です。計算中も画像の閲覧や計算済み深度の移動ができます。")
        self.stop_button = button("計算を中断", self.stop,
            "バックグラウンドの計算を中断します。実行中のFFTまたは相関ブロックの終了後に止まります。探索済みの曲線と再生画像は保持します。")
        progress_row.addWidget(self.progress_label)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.stop_button)
        layout.addLayout(progress_row)
        self.optics_label = QLabel()
        session_row = QHBoxLayout()
        session_row.addWidget(self.optics_label)
        self.session_label = QLabel(self.session.path.name)
        self.session_label.setMinimumWidth(0)
        self.session_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.session_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tip(self.session_label, str(self.session.path)+"\n現在の保存セッションです。再生画像とCapture画像を同じセッション内に保存します。")
        session_row.addWidget(self.session_label, 1)
        self.session_button = button("新規セッション", self.new_session,
            "以後の画像を保存する新しいセッションを作ります。現在の入力画像や補正は保持します。カメラ接続中は切断してから操作してください。")
        session_row.addWidget(self.session_button)
        layout.addLayout(session_row)
        self.message = QLabel("Capture または「画像を読込」から開始できます。位相回復はキャリブレーションタブで準備します。")
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.message)
        tip(title, "ホイール: 拡大縮小、ドラッグ: 移動、N: コントラスト切替、Q / Esc: 終了。各操作の説明はマウスを重ねて表示します。")
        self.shortcuts = []
        for key, callback in (("N", self.toggle_contrast), ("Q", self.close), ("Esc", self.close)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll_camera)
        self.timer.start()
        self.building = False
        self.update_controls()
        if config.calibration_file:
            QTimer.singleShot(0, lambda: self.calibration_tab.load_calibration(config.calibration_file))

    @property
    def workspaces(self):
        return (self.acquisition, self.calibration_tab)

    def notify(self, text):
        self.message.setText(str(text))

    def phase_reason(self, pair):
        try:
            if self.calibration is None:
                raise ValueError("キャリブレーションタブで画像変換を適用してください")
            if self.config.plane_separation_mm is None:
                raise ValueError("キャリブレーションの面間距離が必要です")
            if pair is None:
                raise ValueError("Capture または画像の読み込みが必要です")
            self.calibration.validate_for(pair, self.config)
        except Exception as exc:
            return str(exc)
        return ""

    def update_controls(self):
        if self.building:
            return
        busy = self.worker is not None
        for workspace in self.workspaces:
            workspace.update_controls()
        self.stop_button.setEnabled(busy and self.kind in ("Analyze", "焦点探索", "深度再生", "ベクトルマップ"))
        self.connect_button.setEnabled(not busy)
        self.connect_button.setText("カメラを切断" if self.camera else "カメラを接続")
        self.simulate_button.setEnabled(not busy and self.camera is None)
        self.settings_button.setEnabled(not busy)
        self.session_button.setEnabled(not busy and self.camera is None)
        c = self.config
        self.optics_label.setText(f"{c.wavelength_nm:g} nm · {c.pixel_pitch_um:g} µm · {c.padding_size}² 平均値パディング · CPU {c.compute_threads} スレッド")
        tip(self.optics_label, "GS往復は帯域制限なし。各深度の再生は、あり／なし両方を保持します。設定ボタンから各条件を変更できます。")

    def submit(self, owner, kind, operation, callback):
        if self.worker is not None:
            self.notify("現在の処理を中断するか、完了後に操作してください。")
            return False
        revision = owner.revision if owner else 0
        self.kind, self.owner = kind, owner
        worker = Worker(operation, self)
        self.worker = worker
        self.progress.setRange(0, 0)
        self.progress_label.setText(kind)
        valid = lambda: not self.closing and (owner is None or revision == owner.revision)
        def progress(stage, current, total, data):
            if not valid():
                return
            self.progress.setRange(0, total)
            self.progress.setValue(current)
            self.progress_label.setText(f"{stage} {current}/{total}" if total else stage)
            if owner:
                owner.on_progress(stage, current, total, data)
        def success(result):
            if kind == "カメラ接続" or valid():
                callback(result)
        def failed(error):
            if valid():
                self.notify(error)
                self.progress_label.setText("処理できませんでした")
                if owner:
                    for viewer in owner.viewers:
                        viewer.pending_depth = None
        def cancelled():
            if valid():
                self.progress_label.setText("中断しました")
        worker.progress.connect(progress)
        worker.succeeded.connect(success)
        worker.failed.connect(failed)
        worker.cancelled.connect(cancelled)
        worker.finished.connect(self.finished)
        self.update_controls()
        worker.start()
        return True

    def finished(self):
        old = self.worker
        self.worker = self.owner = None
        if old:
            old.deleteLater()
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        if self.progress_label.text() not in ("中断しました", "処理できませんでした"):
            self.progress_label.setText("待機中")
        self.update_controls()
        if not self.closing:
            # Latest requested uncached depth wins; cached browsing never waits.
            for workspace in self.workspaces:
                workspace.process_pending()
                if self.worker is not None:
                    break

    def stop(self):
        for workspace in self.workspaces:
            for viewer in workspace.viewers:
                viewer.pending_depth = None
                if viewer.rendered is not None:
                    viewer.select_depth(viewer.rendered.z_mm)
        if self.worker:
            self.worker.cancel()
            self.progress_label.setText("現在のFFT / 相関ブロックの完了後に中断…")

    def connect_camera(self):
        if self.camera:
            camera, self.camera = self.camera, None
            self.live_pair = None
            self.submit(None, "カメラ切断", lambda c, p: camera.close(), lambda r: self.state_label.setText("OFFLINE"))
        else:
            config, session = self.config, self.session
            self.submit(None, "カメラ接続", lambda c, p: NativeCamera(config, session), self.camera_connected)

    def start_simulation(self):
        if self.camera is None and self.worker is None:
            self.camera_connected(SimulatedCamera(self.config, self.session))

    def start_native_simulation(self):
        if self.camera is None and self.worker is None:
            self.submit(None, "カメラ接続", lambda c, p: NativeCamera(self.config, self.session, simulate=True), self.camera_connected)

    def camera_connected(self, camera):
        self.camera = camera
        if not self.closing:
            self.state_label.setText("SIMULATION · 10 Hz" if isinstance(camera, SimulatedCamera) else "LIVE · 外部トリガ")
            self.notify("各タブのCaptureで、そのタブに最新の同期ペアを固定できます。")
            self.update_controls()

    def poll_camera(self):
        if self.camera is None or self.closing:
            return
        try:
            pair = self.camera.poll()
            if pair is not None:
                self.live_pair, self.last_live = pair, time.monotonic()
                workspace = self.tabs.currentWidget()
                if workspace.pair is None:
                    workspace.show_pair(pair, live=True)
                    workspace.input_status.setText("ライブ表示 · 10 Hz")
            status = self.camera.status()
            if status['error']:
                self.notify(status['error'])
            if time.monotonic()-self.last_live > .3:
                self.state_label.setText("接続中 · 新しい外部トリガを待機")
            else:
                self.state_label.setText("SIMULATION · 10 Hz" if isinstance(self.camera, SimulatedCamera) else "LIVE · 同期ペア")
        except Exception as exc:
            self.notify(str(exc))
        self.update_controls()

    def tab_changed(self):
        workspace = self.tabs.currentWidget()
        if workspace.pair is None and self.live_pair:
            workspace.show_pair(self.live_pair, live=True)
        self.update_controls()

    def toggle_contrast(self):
        self.tabs.currentWidget().toggle_contrast()

    def apply_settings(self, config):
        config.validate()
        camera_keys = ('serial0', 'serial1', 'camera_library', 'pair_tolerance_ms', 'max_clock_uncertainty_ms', 'output_dir')
        if self.camera and any(getattr(config, k) != getattr(self.config, k) for k in camera_keys):
            raise ValueError("カメラと保存先の設定を変更する前に、カメラを切断してください。")
        physical_keys = ('wavelength_nm', 'pixel_pitch_um', 'padding_size', 'serial0', 'serial1', 'plane_separation_mm')
        physical_changed = any(getattr(config, k) != getattr(self.config, k) for k in physical_keys)
        changed_output = config.output_dir != self.config.output_dir
        for workspace in self.workspaces:
            workspace.clear_results()
        if physical_changed:
            self.calibration = None
            config = replace(config, plane_separation_mm=None)
        self.config = config
        if changed_output:
            self.new_session()
        for workspace in self.workspaces:
            for widget, value in ((workspace.minimum, config.scan_min_mm), (workspace.maximum, config.scan_max_mm),
                                  (workspace.step, config.scan_step_mm), (workspace.iterations, config.gs_iterations)):
                widget.setValue(value)
        self.notify("設定を更新しました。" + ("光学条件が変わったため、キャリブレーションを再適用または再計算してください。" if physical_changed else ""))
        self.update_controls()

    def edit_settings(self):
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.apply_settings(dialog.result_config)
            except Exception as exc:
                self.notify(str(exc))

    def load_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, "YAML設定を読込", str(self.config_path), "YAML (*.yaml *.yml)")
        if path:
            try:
                config = load_config(Path(path))
                self.apply_settings(config)
                self.config_path = Path(path)
                if config.calibration_file:
                    self.calibration_tab.load_calibration(config.calibration_file)
            except Exception as exc:
                self.notify(str(exc))

    def save_settings(self):
        path, _ = QFileDialog.getSaveFileName(self, "YAML設定を保存", str(self.config_path), "YAML (*.yaml *.yml)")
        if path:
            try:
                workspace = self.tabs.currentWidget()
                config = replace(self.config, scan_min_mm=workspace.minimum.value(), scan_max_mm=workspace.maximum.value(),
                    scan_step_mm=workspace.step.value(), gs_iterations=workspace.iterations.value()).validate()
                save_config(config, Path(path))
                self.notify(f"設定を保存しました: {path}")
            except Exception as exc:
                self.notify(str(exc))

    def new_session(self):
        if self.camera or self.worker:
            self.notify("カメラ切断と計算完了後に新しいセッションを作成できます。")
            return
        self.session = Session(self.config.output_dir)
        self.session_label.setText(self.session.path.name)
        tip(self.session_label, str(self.session.path))
        self.notify("新しい保存セッションを選択しました。")

    def closeEvent(self, event):
        if self.allow_close:
            event.accept()
            return
        event.ignore()
        if self.closing:
            return
        self.closing = True
        self.timer.stop()
        for workspace in self.workspaces:
            for viewer in workspace.viewers:
                viewer.pending_depth = None
        if self.worker:
            self.worker.cancel()
        self.notify("計算を停止し、カメラ接続を終了しています…")
        QTimer.singleShot(0, self.close_when_ready)

    def close_when_ready(self):
        if self.worker is not None:
            QTimer.singleShot(50, self.close_when_ready)
            return
        if self.camera:
            camera, self.camera = self.camera, None
            self.submit(None, "カメラ終了", lambda c, p: camera.close(), lambda r: None)
            QTimer.singleShot(50, self.close_when_ready)
            return
        for workspace in self.workspaces:
            workspace.clear_results()
        self.allow_close = True
        self.close()
