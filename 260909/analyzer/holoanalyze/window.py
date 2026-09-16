"""Paired capture/freeze, calibration and responsive reconstruction workspace."""
from dataclasses import asdict, replace
from pathlib import Path
import time

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
    QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget)

from .calibration import Calibration, build_calibration
from .camera import NativeCamera, SimulatedCamera
from .config import load_config, save_config
from .data import Frame, ImagePair, Session, fingerprint, load_pair, read_image, save_result
from .engine import analyze, depths
from .widgets import FocusPlot, ImageView, qimage
from .workers import Worker


def button(text, callback):
    result = QPushButton(text)
    result.clicked.connect(callback)
    return result


def number(value, low=-100000, high=100000, decimals=3):
    result = QDoubleSpinBox()
    result.setDecimals(decimals)
    result.setRange(low, high)
    result.setValue(value)
    result.setKeyboardTracking(False)
    result.setSuffix(" mm")
    return result


class MainWindow(QMainWindow):
    def __init__(self, config, config_path):
        super().__init__()
        self.config, self.config_path = config, Path(config_path)
        self.session = Session(config.output_dir)
        self.camera = None
        self.live_pair = self.frozen_pair = None
        self.last_live = 0
        self.calibration = None
        self.analysis = self.rendered = None
        self.analysis_metadata = None
        self.worker = None
        self.generation = 0
        self.pending_depth = None
        self.normalize = True
        self.closing = self.allow_close = False
        self.kind = ""
        self.setWindowTitle("DualHolo Analyze")
        self.resize(1480, 1000)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        header = QHBoxLayout()
        title = QLabel("<b>DualHolo Analyze</b>　CPU holography")
        header.addWidget(title)
        header.addStretch()
        self.state_label = QLabel("OFFLINE · カメラなしで画像を読み込めます")
        header.addWidget(self.state_label)
        layout.addLayout(header)
        toolbar = QHBoxLayout()
        self.connect_button = button("Connect cameras", self.connect_camera)
        self.simulate_button = button("Simulate 10 Hz", self.start_simulation)
        self.capture_button = button("Capture", self.capture)
        self.resume_button = button("Resume", self.resume)
        self.record_button = button("● Rec OFF", self.toggle_record)
        self.load_button = button("Load image / pair…", self.load_image)
        self.other_button = button("Load cam1…", lambda: self.load_single(1))
        for w in (self.connect_button, self.simulate_button, self.capture_button, self.resume_button,
                  self.record_button, self.load_button, self.other_button):
            toolbar.addWidget(w)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        vertical = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(vertical, 1)
        cameras = QSplitter(Qt.Orientation.Horizontal)
        self.camera_views, self.camera_labels = [], []
        for i in range(2):
            panel = QWidget()
            box = QVBoxLayout(panel)
            label = QLabel(f"cam{i} · {getattr(config, f'serial{i}')} · no image")
            view = ImageView()
            box.addWidget(label)
            box.addWidget(view)
            cameras.addWidget(panel)
            self.camera_views.append(view)
            self.camera_labels.append(label)
        vertical.addWidget(cameras)
        work = QSplitter(Qt.Orientation.Horizontal)
        vertical.addWidget(work)
        settings = QWidget()
        controls = QVBoxLayout(settings)
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem("Gabor · cam0", "gabor_cam0")
        self.mode.addItem("Gabor · cam1", "gabor_cam1")
        self.mode.addItem("Phase recovery · 2 cameras", "phase")
        self.mode.currentIndexChanged.connect(self.mode_changed)
        form.addRow("Mode", self.mode)
        self.minimum = number(config.scan_min_mm)
        self.maximum = number(config.scan_max_mm)
        self.step = number(config.scan_step_mm, .001, 100000)
        form.addRow("Minimum", self.minimum)
        form.addRow("Maximum", self.maximum)
        form.addRow("Interval", self.step)
        self.iterations = QSpinBox()
        self.iterations.setRange(1, 10000)
        self.iterations.setValue(config.gs_iterations)
        form.addRow("GS iterations", self.iterations)
        controls.addLayout(form)
        row = QHBoxLayout()
        self.analyze_button = button("Analyze", self.start_analysis)
        self.stop_button = button("Stop", self.stop)
        row.addWidget(self.analyze_button)
        row.addWidget(self.stop_button)
        controls.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        controls.addWidget(self.progress)
        self.progress_label = QLabel("Ready")
        self.progress_label.setWordWrap(True)
        controls.addWidget(self.progress_label)
        controls.addSpacing(12)
        self.filtered = QCheckBox("2D anti-alias filter")
        self.filtered.setChecked(True)
        self.filtered.toggled.connect(self.show_render)
        controls.addWidget(self.filtered)
        explanation = QLabel("ON / OFF は同じ深度の計算済み画像を切替。\nGS の往復伝搬には常にフィルタを適用。")
        explanation.setWordWrap(True)
        controls.addWidget(explanation)
        self.depth = number((config.scan_min_mm+config.scan_max_mm)/2)
        self.depth.editingFinished.connect(self.depth_entered)
        depth_form = QFormLayout()
        depth_form.addRow("Depth", self.depth)
        controls.addLayout(depth_form)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 10000)
        self.slider.setValue(5000)
        self.slider.valueChanged.connect(self.slider_changed)
        controls.addWidget(self.slider)
        self.peak_label = QLabel("Tamura peak: —")
        self.peak_label.setWordWrap(True)
        controls.addWidget(self.peak_label)
        self.peak_button = button("Go to Tamura peak", self.go_to_peak)
        controls.addWidget(self.peak_button)
        controls.addSpacing(12)
        calibration_box = QGroupBox("Glass-plate calibration")
        cl = QVBoxLayout(calibration_box)
        self.calibration_label = QLabel("未読込 · 位相回復は無効")
        self.calibration_label.setWordWrap(True)
        cl.addWidget(self.calibration_label)
        self.build_calibration_button = button("Calibrate captured glass…", self.calibrate)
        self.load_calibration_button = button("Load calibration…", self.load_calibration)
        self.save_calibration_button = button("Save calibration…", self.save_calibration)
        for w in (self.build_calibration_button, self.load_calibration_button, self.save_calibration_button):
            cl.addWidget(w)
        controls.addWidget(calibration_box)
        controls.addStretch()
        controls.addWidget(button("Load YAML config…", self.load_settings))
        controls.addWidget(button("Save YAML config…", self.save_settings))
        self.optics_label = QLabel()
        self.optics_label.setWordWrap(True)
        controls.addWidget(self.optics_label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(settings)
        scroll.setMinimumWidth(350)
        work.addWidget(scroll)
        reconstruction = QWidget()
        rlayout = QVBoxLayout(reconstruction)
        rtools = QHBoxLayout()
        self.result_label = QLabel("Reconstruction · 画像領域のみ表示")
        rtools.addWidget(self.result_label, 1)
        self.result_view = ImageView()
        rtools.addWidget(button("−", lambda: self.result_view.zoom(1/1.25)))
        rtools.addWidget(button("+", lambda: self.result_view.zoom(1.25)))
        rtools.addWidget(button("Fit", self.result_view.fit_image))
        rtools.addWidget(button("1:1", self.result_view.original_size))
        rlayout.addLayout(rtools)
        rlayout.addWidget(self.result_view, 1)
        export = QHBoxLayout()
        self.copy_button = button("Copy image", self.copy_image)
        self.save_button = button("Save image as…", self.save_image)
        self.raw_button = button("Save captured raw pair", self.save_raw)
        export.addWidget(self.copy_button)
        export.addWidget(self.save_button)
        export.addWidget(self.raw_button)
        export.addStretch()
        rlayout.addLayout(export)
        self.plot = FocusPlot()
        rlayout.addWidget(self.plot)
        work.addWidget(reconstruction)
        work.setSizes([350, 1060])
        vertical.setSizes([285, 650])
        session_row = QHBoxLayout()
        self.session_label = QLabel(str(self.session.path))
        self.session_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        session_row.addWidget(self.session_label, 1)
        self.session_button = button("New session", self.new_session)
        session_row.addWidget(self.session_button)
        layout.addLayout(session_row)
        self.message = QLabel("Load image / pair、または Simulate で開始できます。カメラ設定は変更しません。")
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.message)
        layout.addWidget(QLabel("R: Rec start / stop　 N: display contrast　 Q / Esc: Quit　 |　Wheel: zoom　 Drag / scrollbars: pan"))
        self.shortcuts = []
        for key, action in (("R", self.toggle_record), ("N", self.toggle_contrast), ("Q", self.close), ("Esc", self.close)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(action)
            self.shortcuts.append(shortcut)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll_camera)
        self.timer.start()
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(config.slider_debounce_ms)
        self.debounce.timeout.connect(self.request_depth)
        if config.calibration_file:
            try:
                self.calibration = Calibration.load(config.calibration_file)
            except Exception as exc:
                self.notify(f"Calibration not loaded: {exc}")
        self.update_controls()

    def notify(self, text):
        self.message.setText(str(text))

    def phase_reason(self):
        try:
            if self.frozen_pair is None:
                raise ValueError("Capture または画像の読み込みが必要です")
            if self.calibration is None:
                raise ValueError("キャリブレーションが必要です")
            self.calibration.validate_for(self.frozen_pair, self.config)
            if self.config.plane_separation_mm is None:
                raise ValueError("YAML に符号付き plane_separation_mm を設定してください")
        except Exception as exc:
            return str(exc)
        return ""

    def update_controls(self):
        busy = self.worker is not None
        frozen = self.frozen_pair is not None
        phase_reason = self.phase_reason()
        self.mode.model().item(2).setEnabled(not phase_reason)
        self.mode.setItemData(2, phase_reason or "Calibrated two-plane GS", Qt.ItemDataRole.ToolTipRole)
        mode = self.mode.currentData()
        can_analyze = frozen and (not phase_reason if mode == "phase" else self.frozen_pair.frames[0 if mode == "gabor_cam0" else 1] is not None)
        self.analyze_button.setEnabled(can_analyze and not busy)
        self.stop_button.setEnabled(busy and self.kind in ("Analyze", "Depth", "Calibration"))
        self.capture_button.setEnabled(self.camera is not None and not frozen and self.live_pair is not None and time.monotonic()-self.last_live < .3)
        self.resume_button.setEnabled(frozen)
        self.record_button.setEnabled(self.camera is not None)
        self.connect_button.setText("Disconnect" if self.camera else "Connect cameras")
        self.connect_button.setEnabled(not busy)
        self.simulate_button.setEnabled(not busy and self.camera is None)
        self.load_button.setEnabled(not busy)
        self.other_button.setEnabled(not busy and frozen)
        self.other_button.setText("Load cam0…" if frozen and self.frozen_pair.frames[0] is None else "Load cam1…")
        self.raw_button.setEnabled(frozen and not busy)
        self.build_calibration_button.setEnabled(frozen and all(self.frozen_pair.frames) and not busy)
        self.load_calibration_button.setEnabled(not busy)
        self.save_calibration_button.setEnabled(self.calibration is not None and not busy)
        self.session_button.setEnabled(not busy and self.camera is None)
        self.session_button.setToolTip("Disconnect cameras before starting a new session")
        self.depth.setEnabled(self.analysis is not None)
        self.slider.setEnabled(self.analysis is not None)
        self.peak_button.setEnabled(self.analysis is not None and not busy)
        exportable = self.rendered is not None and not busy and self.pending_depth is None
        self.copy_button.setEnabled(exportable)
        self.save_button.setEnabled(exportable)
        if self.calibration:
            m = self.calibration.metadata
            self.calibration_label.setText(f"{m.get('inliers', '?')} points · RMS {m.get('rms_px', 0):.3f} px\n" +
                                           (phase_reason if phase_reason else "位相回復を使用できます"))
        else:
            self.calibration_label.setText("未読込 · 位相回復は無効")
        self.optics_label.setText(f"{self.config.wavelength_nm:g} nm · {self.config.pixel_pitch_um:g} µm\n"
                                 f"4096 × 4096 mean padding\ncam0 → cam1: {self.config.plane_separation_mm} mm")

    def submit(self, kind, operation, callback):
        if self.worker is not None:
            self.notify("現在の処理を停止してから操作してください。")
            return
        generation = self.generation
        self.kind = kind
        worker = Worker(operation, self)
        self.worker = worker
        self.progress.setRange(0, 0)
        self.progress_label.setText(kind)
        worker.progress.connect(lambda *args: self.on_progress(generation, *args))
        # A Connect result owns live acquisition threads. Retain it even when
        # closing or Resume invalidated the view; shutdown must join those threads.
        worker.succeeded.connect(lambda result: callback(result) if kind == "Connect" or
                                 (generation == self.generation and not self.closing) else None)
        worker.failed.connect(lambda error: self.notify(error) if generation == self.generation else None)
        worker.cancelled.connect(lambda: self.progress_label.setText("Stopped") if generation == self.generation else None)
        worker.finished.connect(self.finished)
        self.update_controls()
        worker.start()

    def finished(self):
        old = self.worker
        self.worker = None
        if old:
            old.deleteLater()
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        if self.progress_label.text() not in ("Stopped", "Scan stopped; partial curve retained"):
            self.progress_label.setText("Ready")
        if self.pending_depth is not None and self.analysis is not None and not self.closing:
            self.request_depth()
        self.update_controls()

    def on_progress(self, generation, stage, current, total, row):
        if generation != self.generation or self.closing:
            return
        self.progress.setRange(0, total)
        self.progress.setValue(current)
        self.progress_label.setText(f"{stage}  {current}/{total}" if total else stage)
        if row is not None:
            self.plot.rows.append(row)
            self.plot.update()

    def invalidate(self):
        self.generation += 1
        if self.worker:
            self.worker.cancel()
        self.pending_depth = None
        self.debounce.stop()
        self.analysis = self.rendered = self.analysis_metadata = None
        self.result_view.clear()
        self.plot.rows = []
        self.plot.depth = None
        self.plot.update()
        self.peak_label.setText("Tamura peak: —")
        self.result_label.setText("Reconstruction · 画像領域のみ表示")

    def connect_camera(self):
        if self.camera:
            camera, self.camera = self.camera, None
            self.live_pair = None
            self.submit("Disconnect", lambda c, p: camera.close(), lambda r: self.state_label.setText("OFFLINE"))
        else:
            self.submit("Connect", lambda c, p: NativeCamera(self.config, self.session), self.camera_connected)

    def start_simulation(self):
        if self.camera is None and self.worker is None:
            self.camera_connected(SimulatedCamera(self.config, self.session))

    def start_native_simulation(self):
        if self.camera is None and self.worker is None:
            self.submit("Connect", lambda c, p: NativeCamera(self.config, self.session, simulate=True), self.camera_connected)

    def camera_connected(self, camera):
        self.camera = camera
        if self.closing:
            return
        self.state_label.setText("SIMULATION · 10 Hz" if isinstance(camera, SimulatedCamera) else "LIVE · external trigger")
        self.notify("Capture で最新の同期ペアを固定します。")
        self.update_controls()

    def poll_camera(self):
        if not self.camera or self.closing:
            return
        try:
            pair = self.camera.poll()
            if pair:
                self.live_pair, self.last_live = pair, time.monotonic()
                if self.frozen_pair is None:
                    self.show_pair(pair)
            status = self.camera.status()
            self.record_button.setText("■ STOP REC" if status["recording"] else "● Rec OFF")
            self.record_button.setStyleSheet("background:#b33138;color:white" if status["recording"] else "")
            if status["error"]:
                self.notify(status["error"])
            self.record_button.setToolTip(f"Saved cam0/1: {status['saved']} · pending {status['pending']} · gaps {status['gaps']} · disk waits {status['waits']}")
            if not self.frozen_pair:
                self.state_label.setText(("SIMULATION" if isinstance(self.camera, SimulatedCamera) else "LIVE") +
                                         (" · waiting for external trigger" if time.monotonic()-self.last_live > .3 else " · paired preview"))
        except Exception as exc:
            self.notify(str(exc))
        self.update_controls()

    def show_pair(self, pair):
        for i, frame in enumerate(pair.frames):
            if frame:
                self.camera_views[i].set_image(frame.image, self.normalize)
                h, w = frame.image.shape
                self.camera_labels[i].setText(f"cam{i} · {frame.serial} · {w} × {h} · frame {frame.frame_id}")
            else:
                self.camera_views[i].clear()
                self.camera_labels[i].setText(f"cam{i} · no image")

    def capture(self):
        if self.live_pair is None or time.monotonic()-self.last_live > .3:
            self.notify("新しい同期ペアがありません。外部トリガとカメラ接続を確認してください。")
            return
        self.invalidate()
        self.frozen_pair = self.live_pair
        self.show_pair(self.frozen_pair)
        self.state_label.setText("CAPTURED · " + self.frozen_pair.captured_at)
        self.notify("ペアを固定しました。Analyze またはキャリブレーションを実行できます。")
        self.update_controls()

    def resume(self):
        self.invalidate()
        self.frozen_pair = None
        if self.live_pair:
            self.show_pair(self.live_pair)
        else:
            for view in self.camera_views:
                view.clear()
        self.state_label.setText("LIVE" if self.camera else "OFFLINE")
        self.notify("Capture画像と解析結果を破棄しました。保存済みファイルは保持しています。")
        self.update_controls()

    def toggle_record(self):
        if self.camera:
            self.camera.record(not self.camera.status()["recording"])

    def toggle_contrast(self):
        self.normalize = not self.normalize
        if self.frozen_pair or self.live_pair:
            self.show_pair(self.frozen_pair or self.live_pair)
        self.show_render()

    def load_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select either camera image", "", "Images (*.tif *.tiff *.png *.bmp)")
        if path:
            self.invalidate()
            self.submit("Load", lambda c, p: load_pair(path, self.config), self.pair_loaded)

    def pair_loaded(self, result):
        self.frozen_pair, note = result
        self.show_pair(self.frozen_pair)
        self.state_label.setText("LOADED · " + self.frozen_pair.captured_at)
        if self.frozen_pair.frames[0] is None:
            self.mode.setCurrentIndex(1)
        self.notify(note)
        self.update_controls()

    def load_single(self, camera):
        if self.frozen_pair is None:
            return
        # If cam0 is missing, the same button supplies that missing member.
        camera = 0 if self.frozen_pair.frames[0] is None else camera
        path, _ = QFileDialog.getOpenFileName(self, f"Select cam{camera}", "", "Images (*.tif *.tiff *.png *.bmp)")
        if not path:
            return
        frames = list(self.frozen_pair.frames)
        self.invalidate()
        def operation(cancel, progress):
            frames[camera] = Frame(read_image(path), getattr(self.config, f"serial{camera}"), path=path)
            return ImagePair(tuple(frames)), "Manually paired images; verify that these are the same exposure."
        self.submit("Load", operation, self.pair_loaded)

    def mode_changed(self):
        self.invalidate()
        self.update_controls()

    def scan_depths(self):
        return depths(self.minimum.value(), self.maximum.value(), self.step.value())

    def start_analysis(self):
        if self.worker or self.frozen_pair is None:
            return
        try:
            scan = self.scan_depths()
            mode = self.mode.currentData()
            if mode == "phase" and self.phase_reason():
                raise ValueError(self.phase_reason())
            pair, config, calibration = self.frozen_pair, self.config, self.calibration
            iterations = self.iterations.value()
            self.invalidate()
            self.analysis_metadata = dict(mode=mode, input_token=pair.token, config=asdict(config),
                                          iterations=iterations, scan_mm=[self.minimum.value(), self.maximum.value(), self.step.value()],
                                          calibration=calibration.metadata if mode == "phase" else None)
            self.submit("Analyze", lambda c, p: analyze(pair, mode, config, calibration, iterations, scan, c, p), self.analysis_done)
        except Exception as exc:
            self.notify(str(exc))

    def analysis_done(self, result):
        self.analysis = result
        self.plot.rows = list(result.curve)
        self.progress_label.setText("Scan stopped; partial curve retained" if result.stopped else "Ready")
        self.rendered = result.best_filtered if self.filtered.isChecked() else result.best_unfiltered
        if self.rendered:
            self.set_depth(self.rendered.z_mm)
        self.show_render()
        z = self.rendered.z_mm if self.rendered else None
        boundary = z is not None and (z == result.curve[0][0] or z == result.curve[-1][0])
        self.notify(("途中までの結果です。" if result.stopped else "探索が完了しました。") +
                    ("最大値が探索端にあります。深度を確定する前に範囲を広げてください。" if boundary else ""))

    def set_depth(self, value):
        self.depth.blockSignals(True)
        self.depth.setValue(value)
        self.depth.blockSignals(False)
        lo, hi = self.minimum.value(), self.maximum.value()
        self.slider.blockSignals(True)
        self.slider.setValue(round(10000*(value-lo)/max(hi-lo, 1e-9)))
        self.slider.blockSignals(False)

    def slider_changed(self, value):
        self.depth.setValue(self.minimum.value()+(self.maximum.value()-self.minimum.value())*value/10000)
        self.debounce.start()

    def depth_entered(self):
        self.set_depth(self.depth.value())
        self.debounce.stop()
        self.request_depth()

    def request_depth(self):
        if not self.analysis or self.closing:
            return
        z = self.depth.value()
        self.pending_depth = z
        if self.worker:
            if self.kind == "Depth":
                self.worker.cancel()
            return
        self.pending_depth = None
        reconstruction = self.analysis.reconstruction
        self.notify(f"z = {z:.3f} mm を計算中…")
        def completed(result):
            if self.pending_depth is None:
                self.rendered = result
                self.show_render()
                self.notify(f"z = {result.z_mm:.3f} mm")
        self.submit("Depth", lambda c, p: reconstruction.render(z, c), completed)

    def stop(self):
        self.debounce.stop()
        self.pending_depth = None
        if self.worker:
            self.worker.cancel()
            self.progress_label.setText("Stopping after the current FFT / matching block…")

    def show_render(self, *_):
        if self.rendered is None:
            return
        filtered = self.filtered.isChecked()
        image = self.rendered.filtered if filtered else self.rendered.unfiltered
        # Use common contrast bounds for a fair, smooth A/B comparison.
        limits = np.percentile(self.rendered.filtered, [1, 99]) if self.normalize else (0, max(float(self.rendered.filtered.max()), float(self.rendered.unfiltered.max())))
        self.result_view.set_image(image, self.normalize, limits)
        self.result_label.setText(f"z = {self.rendered.z_mm:.3f} mm · {'2D filtered' if filtered else 'Unfiltered comparison'}")
        self.plot.depth = self.rendered.z_mm
        self.plot.update()
        if self.analysis:
            best = self.analysis.best_filtered if filtered else self.analysis.best_unfiltered
            if best:
                self.peak_label.setText(f"Tamura peak: {best.z_mm:.3f} mm" + (" · partial scan" if self.analysis.stopped else ""))
        self.update_controls()

    def go_to_peak(self):
        if self.analysis:
            self.rendered = self.analysis.best_filtered if self.filtered.isChecked() else self.analysis.best_unfiltered
            self.set_depth(self.rendered.z_mm)
            self.show_render()

    def calibrate(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Calibrate captured glass plate")
        layout = QVBoxLayout(dialog)
        info = QLabel("ガラスプレートの同期ペアを使用します。\n各カメラを焦点面にGabor再生して対応点を求めます。\ncam0のReverseYは保存済み画像に適用済みです。")
        layout.addWidget(info)
        auto = QCheckBox("現在の深度範囲から各カメラの焦点を探索")
        auto.setChecked(True)
        layout.addWidget(auto)
        form = QFormLayout()
        z0 = number(self.depth.value())
        z1 = number(self.depth.value())
        form.addRow("cam0 focus", z0)
        form.addRow("cam1 focus", z1)
        z0.setEnabled(False)
        z1.setEnabled(False)
        auto.toggled.connect(lambda checked: [w.setEnabled(not checked) for w in (z0, z1)])
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            scan = self.scan_depths()
            focus = None if auto.isChecked() else (z0.value(), z1.value())
            pair, config = self.frozen_pair, self.config
            self.invalidate()
            self.submit("Calibration", lambda c, p: build_calibration(pair, config, focus, scan, c, p), self.calibration_ready)
        except Exception as exc:
            self.notify(str(exc))

    def calibration_ready(self, calibration):
        self.calibration = calibration
        m = calibration.metadata
        self.notify(f"Calibration ready: {m['inliers']} points · RMS {m['rms_px']:.3f} px · spatial holdout {m['holdout_rms_px']:.3f} px. Save calibration で再利用できます。")
        self.update_controls()

    def load_calibration(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load calibration", "", "Calibration (*.npz)")
        if path:
            self.invalidate()
            self.submit("Load calibration", lambda c, p: Calibration.load(path), self.calibration_ready)

    def save_calibration(self):
        try:
            self.session.ensure(self.config)
        except Exception as exc:
            self.notify(str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save calibration", str(self.session.path / "calibration.npz"), "Calibration (*.npz)")
        if path:
            calibration = self.calibration
            self.submit("Save calibration", lambda c, p: calibration.save(path), lambda r: self.notify(f"Saved: {path}"))

    def load_settings(self):
        if self.worker or self.camera:
            self.notify("設定変更前に計算を停止し、カメラをDisconnectしてください。")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load YAML config", str(self.config_path), "YAML (*.yaml *.yml)")
        if path:
            try:
                config = load_config(Path(path))
                calibration = Calibration.load(config.calibration_file) if config.calibration_file else None
                self.invalidate()
                changed_output = config.output_dir != self.config.output_dir
                self.config, self.config_path = config, Path(path)
                self.calibration = calibration
                if changed_output:
                    self.new_session()
                for w, value in ((self.minimum, config.scan_min_mm), (self.maximum, config.scan_max_mm),
                                 (self.step, config.scan_step_mm), (self.iterations, config.gs_iterations)):
                    w.setValue(value)
                self.debounce.setInterval(config.slider_debounce_ms)
                self.notify(f"Config loaded: {path}")
                self.update_controls()
            except Exception as exc:
                self.notify(str(exc))

    def save_settings(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save YAML config", str(self.config_path), "YAML (*.yaml *.yml)")
        if path:
            try:
                config = replace(self.config, scan_min_mm=self.minimum.value(), scan_max_mm=self.maximum.value(),
                                 scan_step_mm=self.step.value(), gs_iterations=self.iterations.value())
                config.validate()
                save_config(config, Path(path))
                self.notify(f"Config saved: {path}")
            except Exception as exc:
                self.notify(str(exc))

    def copy_image(self):
        if self.rendered is not None and not self.worker:
            QApplication.clipboard().setImage(qimage(self.result_view.pixels))
            self.notify("表示コントラストの画像をクリップボードへコピーしました。")

    def save_image(self):
        if self.rendered is None or self.worker:
            return
        filtered = self.filtered.isChecked()
        default = self.session.default_result(self.frozen_pair, self.analysis_metadata["mode"], self.rendered.z_mm, filtered)
        try:
            self.session.ensure(self.config)
            default.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.notify(str(exc))
            return
        path, selected_filter = QFileDialog.getSaveFileName(self, "Save reconstruction", str(default), "Float32 TIFF (*.tiff);;Display PNG (*.png)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".png" if "PNG" in selected_filter else ".tiff"
        rendered, pair = self.rendered, self.frozen_pair
        pixels = self.result_view.pixels.copy()
        image = rendered.filtered if filtered else rendered.unfiltered
        curve = list(self.analysis.curve)
        metadata = dict(self.analysis_metadata, z_mm=rendered.z_mm, filtered=filtered,
                        scan_stopped=self.analysis.stopped, padding="4096x4096 centered mean of input field",
                        intensity_units="raw DN", contrast_normalized=self.normalize,
                        output_kind="float32 intensity" if Path(path).suffix.lower() in (".tif", ".tiff") else "8-bit display",
                        sources=[f.path if f else None for f in pair.frames])
        def operation(cancel, progress):
            self.session.ensure(self.config)
            metadata["input_sha256"] = [fingerprint(f.image) if f else None for f in pair.frames]
            save_result(path, image, pixels, metadata, curve)
        self.submit("Save", operation, lambda r: self.notify(f"Saved image, parameters and Tamura curve: {path}"))

    def save_raw(self):
        pair, session, config = self.frozen_pair, self.session, self.config
        self.submit("Save raw", lambda c, p: session.save_raw(pair, config), lambda path: self.notify(f"Saved raw pair: {path}"))

    def new_session(self):
        if self.camera or self.worker:
            self.notify("Disconnect cameras before starting a new session.")
            return
        self.session = Session(self.config.output_dir)
        self.session_label.setText(str(self.session.path))
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
        self.debounce.stop()
        self.pending_depth = None
        if self.worker:
            self.worker.cancel()
        self.notify("処理を停止し、録画の保存待ち画像を書き終えてから終了します…")
        # QWidget ignores a recursive close() inside closeEvent. Defer even in
        # offline mode, where there are no workers or cameras to wait for.
        QTimer.singleShot(0, self.close_when_ready)

    def close_when_ready(self):
        if self.worker is not None:
            QTimer.singleShot(50, self.close_when_ready)
            return
        if self.camera:
            camera, self.camera = self.camera, None
            self.submit("Close cameras", lambda c, p: camera.close(), lambda r: None)
            QTimer.singleShot(50, self.close_when_ready)
            return
        self.allow_close = True
        self.close()
