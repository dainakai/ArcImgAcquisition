"""Separate capture and calibration workspaces sharing one camera connection."""
from dataclasses import asdict, replace
from pathlib import Path
import time
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QComboBox, QSpinBox, QScrollArea, QSplitter, QTabWidget, QMenu, QFileDialog, QApplication)
from .calibration import Calibration, CalibrationPreview, build_from_focused, separation_from_focus
from .data import Frame, ImagePair, load_pair, read_image, fingerprint, save_result
from .engine import analyze, depths, Cancelled
from .depth_view import DepthViewer
from .widgets import ImagePanel, VectorPlot, button, number, tip, qimage


class Workspace(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.revision = 0
        self.pair = None
        self.normalize = True
        self.viewers = []
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(4, 8, 4, 4)
        bar = QHBoxLayout()
        self.capture_button = button("Capture · 撮影", self.capture,
            "最新の同期した2カメラ画像を、このタブに固定します。接続中のカメラは外部10 Hzトリガで動作します。保存は「入力画像を保存」で行います。")
        self.resume_button = button("Resume · 再開", self.resume,
            "このタブのCapture画像と解析結果を破棄し、ライブ表示へ戻ります。保存済みファイルと適用済みキャリブレーションは保持します。")
        self.load_button = button("画像を読込…", None,
            "画像ファイルを読み込みます。「ペアを自動選択」ではDualHoloの撮影構造から相方を選びます。任意ファイルはcam0・cam1を個別に指定できます。")
        menu = QMenu(self.load_button)
        for label, slot in (("ペアを自動選択…", lambda: self.load_image(None)),
                            ("cam0 の画像を指定…", lambda: self.load_image(0)),
                            ("cam1 の画像を指定…", lambda: self.load_image(1))):
            action = menu.addAction(label)
            action.triggered.connect(slot)
            action.setToolTip("画像を選択します。現在のタブの解析結果は破棄されます。")
        menu.setToolTipsVisible(True)
        self.load_button.setMenu(menu)
        self.raw_button = button("入力画像を保存…", self.save_raw,
            "固定した元のホログラムを、現在のセッション内のcam0・cam1フォルダとframes.csvへ保存します。画像変換や再反転は行いません。")
        for w in (self.capture_button, self.resume_button, self.load_button, self.raw_button):
            bar.addWidget(w)
        self.input_status = QLabel("画像ファイルの読み込み、またはカメラのCaptureから開始")
        self.input_status.setWordWrap(True)
        bar.addWidget(self.input_status, 1)
        self.root.addLayout(bar)
        self.camera_panels = [ImagePanel(f"cam{i}", source=True) for i in range(2)]

    def update_controls(self):
        busy = self.main.worker is not None
        self.capture_button.setEnabled(not busy and self.pair is None and self.main.camera is not None
            and self.main.live_pair is not None and time.monotonic()-self.main.last_live < .3)
        self.resume_button.setEnabled(self.pair is not None)
        self.load_button.setEnabled(not busy)
        self.raw_button.setEnabled(not busy and self.pair is not None)

    def clear_results(self):
        self.revision += 1
        if self.main.worker is not None and self.main.owner is self:
            self.main.worker.cancel()
        for viewer in self.viewers:
            viewer.clear()

    def set_pair(self, pair, note="画像を読み込みました。"):
        self.clear_results()
        self.pair = pair
        self.show_pair(pair)
        present = ", ".join(f"cam{i}" for i, f in enumerate(pair.frames) if f is not None)
        self.input_status.setText(f"固定済み · {present}")
        self.main.notify(note)
        self.main.update_controls()

    def show_pair(self, pair, live=False):
        for i, (panel, frame) in enumerate(zip(self.camera_panels, pair.frames)):
            panel.show_frame(frame, pair.origin, self.normalize, live)
            if frame:
                panel.title.setText(f"cam{i} · {frame.serial}")
                tip(panel.title, panel.title.text()+f" · {frame.image.shape[1]}×{frame.image.shape[0]} · frame {frame.frame_id}")

    def capture(self):
        if self.main.live_pair is None or time.monotonic()-self.main.last_live > .3:
            self.main.notify("新しい同期ペアがありません。外部トリガと接続を確認してください。")
            return
        live = self.main.live_pair
        self.set_pair(ImagePair(live.frames, origin=live.origin), "このタブに2カメラの画像を固定しました。")

    def resume(self):
        self.clear_results()
        self.pair = None
        if self.main.live_pair:
            self.show_pair(self.main.live_pair, live=True)
        else:
            for panel in self.camera_panels:
                panel.show_frame(None)
        self.input_status.setText("ライブ表示" if self.main.camera else "画像ファイルを読み込めます")
        self.main.notify("このタブの入力画像と解析結果を破棄しました。")
        self.main.update_controls()

    def load_image(self, camera=None):
        title = "DualHoloの片方の画像を選択" if camera is None else f"cam{camera} の画像を選択"
        path, _ = QFileDialog.getOpenFileName(self, title, "", "画像 (*.tif *.tiff *.png *.bmp)")
        if not path:
            return
        path = str(Path(path).resolve())
        config = self.main.config
        old = self.pair
        def operation(cancel, progress):
            if camera is None:
                return load_pair(path, config)
            frames = list(old.frames) if old is not None else [None, None]
            frames[camera] = Frame(read_image(path), getattr(config, f"serial{camera}"), path=path)
            return ImagePair(tuple(frames)), f"cam{camera} を読み込みました。2枚を個別指定した場合は同じ対象の撮影か確認してください。"
        self.main.submit(self, "画像読込", operation, lambda result: self.set_pair(*result))

    def save_raw(self):
        pair, session, config = self.pair, self.main.session, self.main.config
        if pair is not None:
            self.main.submit(self, "入力画像を保存", lambda c, p: session.save_raw(pair, config),
                             lambda path: self.main.notify(f"入力画像を保存しました: {path}"))

    def scan_config(self):
        changes = {f"cam{i}_scan_{edge}_mm": controls[i].value()
                   for edge, controls in (("min", self.minimum), ("max", self.maximum)) for i in (0, 1)}
        return replace(self.main.config, **changes, scan_step_mm=self.step.value(),
                       gs_iterations=self.iterations.value()).validate()

    def bind_viewer(self, viewer):
        self.viewers.append(viewer)
        viewer.depthRequested.connect(lambda z: self.request_depth(viewer))
        viewer.depthChanged.connect(lambda z: self.main.update_controls())

    def request_depth(self, viewer):
        if viewer.pending_depth is None or viewer.analysis is None:
            return
        if self.main.worker is not None:
            if self.main.kind == "深度再生" and self.main.owner is self:
                self.main.worker.cancel()
            return
        z = viewer.pending_depth
        reconstruction = viewer.analysis.reconstruction
        self.main.submit(self, "深度再生", lambda c, p: reconstruction.render(z, c), viewer.accept_render)

    def process_pending(self):
        for viewer in self.viewers:
            if viewer.pending_depth is not None:
                self.request_depth(viewer)
                return

    def on_progress(self, stage, current, total, data):
        pass

    def toggle_contrast(self):
        self.normalize = not self.normalize
        pair = self.pair or self.main.live_pair
        if pair:
            self.show_pair(pair, live=self.pair is None)
        for viewer in self.viewers:
            viewer.toggle_contrast()


def sidebar():
    content = QWidget()
    content.setMinimumWidth(255)
    layout = QVBoxLayout(content)
    layout.setContentsMargins(8, 4, 10, 4)
    layout.setSpacing(10)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setMinimumWidth(277)
    scroll.setMaximumWidth(335)
    scroll.setWidget(content)
    return scroll, layout


def instruction(text):
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def scan_controls(config, layout):
    form = QFormLayout()
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    minimum, maximum = [], []
    for camera in (0, 1):
        lo, hi = config.scan_bounds(camera)
        minimum.append(number(lo))
        maximum.append(number(hi))
        for label, widget in (("最小", minimum[-1]), ("最大", maximum[-1])):
            tip(widget, f"cam{camera} 面からの符号付き伝搬距離です。カメラごとに範囲を指定します。位相回復後の探索はcam0の範囲を使います。")
            form.addRow(f"cam{camera} {label}", widget)
    step = number(config.scan_step_mm, .0001, 100000)
    tip(step, "Tamura探索の共通間隔です。各カメラの最小から最大以下まで、元画像サイズで計算します。再生画像スタックは保持しません。")
    form.addRow("探索間隔", step)
    iterations = QSpinBox()
    iterations.setRange(1, 10000)
    iterations.setValue(config.gs_iterations)
    tip(iterations, "2面GS位相回復の反復回数です。Analyzeを押した時に位相回復を実行します。焦点合わせのGabor再生には使いません。")
    form.addRow("GS反復回数", iterations)
    layout.addLayout(form)
    return minimum, maximum, step, iterations


class AcquisitionWorkspace(Workspace):
    def __init__(self, main):
        super().__init__(main)
        self.analysis_metadata = None
        split = QSplitter(Qt.Orientation.Horizontal)
        self.root.addWidget(split, 1)
        scroll, controls = sidebar()
        self.control_tabs = QTabWidget()
        self.control_tabs.setTabPosition(QTabWidget.TabPosition.North)
        controls.addWidget(self.control_tabs, 1)
        setup_page, depth_page = QWidget(), QWidget()
        setup_controls, depth_controls = QVBoxLayout(setup_page), QVBoxLayout(depth_page)
        for box in (setup_controls, depth_controls):
            box.setContentsMargins(6, 10, 6, 6)
            box.setSpacing(12)
        self.control_tabs.addTab(setup_page, "解析条件")
        self.control_tabs.addTab(depth_page, "深度・保存")
        self.control_tabs.setTabToolTip(0, "再生モードと探索範囲を指定してAnalyzeを実行します。")
        self.control_tabs.setTabToolTip(1, "深度を選んで再生し、表示画像をコピー・保存します。")
        controls = setup_controls
        controls.addWidget(instruction("モードと範囲を指定して、<b>Analyze</b>で深度を探索します。"))
        self.mode = QComboBox()
        self.mode.addItem("Gabor · cam0", "gabor_cam0")
        self.mode.addItem("Gabor · cam1", "gabor_cam1")
        self.mode.addItem("位相回復 · 2カメラ", "phase")
        tip(self.mode, "cam0・cam1の単画像Gabor再生、または2面GS位相回復を選びます。位相回復にはキャリブレーションタブでの画像変換の適用が必要です。")
        self.mode.currentIndexChanged.connect(self.mode_changed)
        controls.addWidget(self.mode)
        self.minimum, self.maximum, self.step, self.iterations = scan_controls(main.config, controls)
        self.analyze_button = button("Analyze · 深度探索", self.start_analysis,
            "元画像サイズ・パディングなしでTamura曲線を計算します。位相回復では最初にGSを実行します。画像スタックは保持せず、選んだ深度を平均値パディングで再生します。途中停止できます。")
        controls.addWidget(self.analyze_button)
        self.calibration_label = instruction("キャリブレーション未適用")
        controls.addWidget(self.calibration_label)
        controls.addWidget(button("キャリブレーションへ", lambda: main.tabs.setCurrentWidget(main.calibration_tab),
            "ガラスプレートの焦点・面間距離・画像変換を求めるタブへ移動します。このタブの入力画像と解析結果は保持します。"))
        controls.addStretch()
        self.copy_button = button("表示画像をコピー", self.copy_image, "現在の再生画像を、表示中のコントラストでクリップボードへコピーします。")
        self.save_button = button("再生画像を保存…", self.save_image, "現在の深度の再生画像を保存します。TIFFはfloat32強度、PNGは表示コントラストです。再生条件JSONとTamura曲線CSVも保存します。")
        self.viewer = DepthViewer("再生画像", layout_mode="external")
        depth_controls.addWidget(self.viewer.details)
        depth_controls.addStretch()
        depth_controls.addWidget(self.copy_button)
        depth_controls.addWidget(self.save_button)
        split.addWidget(scroll)
        inputs = QSplitter(Qt.Orientation.Vertical)
        for panel in self.camera_panels:
            inputs.addWidget(panel)
        inputs.setMinimumWidth(280)
        split.addWidget(inputs)
        self.bind_viewer(self.viewer)
        split.addWidget(self.viewer)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 2)
        split.setSizes([285, 340, 800])

    def mode_changed(self):
        self.clear_results()
        self.analysis_metadata = None
        self.main.update_controls()

    def set_pair(self, pair, note="画像を読み込みました。"):
        super().set_pair(pair, note)
        if pair.frames[0] is None and self.mode.currentData() == "gabor_cam0":
            self.mode.setCurrentIndex(1)

    def update_controls(self):
        super().update_controls()
        busy = self.main.worker is not None
        reason = self.main.phase_reason(self.pair)
        self.mode.model().item(2).setEnabled(not reason)
        self.mode.setItemData(2, reason or "適用済みキャリブレーションで2面GSを実行します", Qt.ItemDataRole.ToolTipRole)
        mode = self.mode.currentData()
        ready = self.pair is not None and (not reason if mode == "phase" else
            self.pair.frames[0 if mode == "gabor_cam0" else 1] is not None)
        self.analyze_button.setEnabled(not busy and ready)
        self.mode.setEnabled(not busy)
        self.iterations.setEnabled(mode == "phase" and not busy)
        can_export = self.viewer.rendered is not None and self.viewer.pending_depth is None
        self.copy_button.setEnabled(can_export)
        self.save_button.setEnabled(can_export and not busy)
        if self.main.calibration is None:
            text = "キャリブレーション未適用\n位相回復を使うには、ガラスプレートの焦点と画像変換を確認して適用してください。"
        else:
            m = self.main.calibration.metadata
            text = f"適用済み · Δz = {self.main.config.plane_separation_mm:+.4f} mm\n補正後RMS {m.get('post_rms_px', m.get('rms_px', 0)):.3f} px"
            if reason:
                text += "\n"+reason
        self.calibration_label.setText(text)

    def start_analysis(self):
        if self.main.worker is not None or self.pair is None:
            return
        try:
            mode = self.mode.currentData()
            camera = 1 if mode == "gabor_cam1" else 0
            scan = depths(self.minimum[camera].value(), self.maximum[camera].value(), self.step.value())
            if mode == "phase" and self.main.phase_reason(self.pair):
                raise ValueError(self.main.phase_reason(self.pair))
            config = self.scan_config()
            pair, calibration = self.pair, self.main.calibration
            self.clear_results()
            self.analysis_metadata = dict(mode=mode, config=asdict(config), input_token=pair.token,
                iterations=config.gs_iterations, calibration=calibration.metadata if mode == "phase" else None)
            self.main.submit(self, "Analyze", lambda c, p: analyze(pair, mode, config, calibration,
                config.gs_iterations, scan, c, p), self.analysis_done)
        except Exception as exc:
            self.main.notify(str(exc))

    def on_progress(self, stage, current, total, data):
        if isinstance(data, tuple):
            self.viewer.plot.rows.append(data)
            self.viewer.plot.update()

    def analysis_done(self, result):
        self.viewer.set_analysis(result)
        self.control_tabs.setCurrentIndex(1)
        self.main.notify(("途中までの結果を保持しました。" if result.stopped else "深度探索が完了しました。")+
            " スライダー・曲線クリックで選んだ深度を平均値パディングで再生します。"+
            (" 山型ピークがないため、焦点は自動確定していません。" if not result.peaks_filtered else ""))

    def copy_image(self):
        if self.viewer.rendered is not None:
            QApplication.clipboard().setImage(qimage(self.viewer.panel.view.pixels))
            self.main.notify("表示画像をクリップボードへコピーしました。")

    def save_image(self):
        viewer = self.viewer
        if viewer.rendered is None or self.analysis_metadata is None:
            return
        filtered = viewer.filtered.isChecked()
        rendered, pair = viewer.rendered, self.pair
        default = self.main.session.default_result(pair, self.analysis_metadata['mode'], rendered.z_mm, filtered)
        try:
            self.main.session.ensure(self.main.config)
            default.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.main.notify(str(exc))
            return
        path, selected = QFileDialog.getSaveFileName(self, "再生画像を保存", str(default), "TIFF 強度 (*.tiff);;PNG 表示画像 (*.png)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".png" if "PNG" in selected else ".tiff"
        image = rendered.filtered if filtered else rendered.unfiltered
        pixels = viewer.panel.view.pixels.copy()
        curve = list(viewer.analysis.curve)
        metadata = dict(self.analysis_metadata, z_mm=rendered.z_mm, filtered=filtered,
            scan_stopped=viewer.analysis.stopped, padding="centered mean of input field",
            gs_bandlimit=False, tamura_padding="none; native input dimensions", contrast_normalized=viewer.normalize,
            sources=[f.path if f else None for f in pair.frames])
        session, config = self.main.session, self.main.config
        def operation(cancel, progress):
            session.ensure(config)
            metadata['input_sha256'] = [fingerprint(f.image) if f else None for f in pair.frames]
            save_result(path, image, pixels, metadata, curve)
        self.main.submit(self, "再生画像を保存", operation, lambda r: self.main.notify(f"画像・条件・Tamura曲線を保存しました: {path}"))


class CalibrationWorkspace(Workspace):
    def __init__(self, main):
        super().__init__(main)
        self.candidate = None
        self.candidate_source = None
        self.focus_signature = None
        split = QSplitter(Qt.Orientation.Horizontal)
        self.root.addWidget(split, 1)
        scroll, controls = sidebar()
        controls.addWidget(instruction("<b>1. ガラスプレートを用意</b><br>このタブでCaptureまたは画像読込。撮影タブの画像とは別に保持します。"))
        self.minimum, self.maximum, self.step, self.iterations = scan_controls(main.config, controls)
        self.scan_button = button("2. 両カメラの焦点を探索", self.scan_focus,
            "ガラスプレートを各カメラ面からGabor再生し、2本のTamura曲線を求めます。山型ピークを候補とし、探索後に各カメラの深度を微調整できます。")
        controls.addWidget(self.scan_button)
        self.gap_label = instruction("cam0 焦点: —\ncam1 焦点: —\n面間距離 Δz: —")
        tip(self.gap_label, "同じガラス面へ再生する符号付き焦点距離から Δz = z0 − z1 を求めます。cam0からcam1へのGS伝搬にこの符号を使います。物理的な離間量は |Δz| です。")
        controls.addWidget(self.gap_label)
        controls.addWidget(instruction("曲線をクリック、スライダー、深度数値で焦点を調整できます。"))
        self.map_button = button("3. ベクトルマップを計算", self.build_map,
            "選択中の2つの焦点像からサブピクセル対応点と二次の画像変換を計算します。元のcam1画像にLanczos4で一度だけ適用し、補正後の残差ベクトルも検証します。")
        controls.addWidget(self.map_button)
        self.quality_label = instruction("ベクトルマップは未計算です。")
        controls.addWidget(self.quality_label)
        self.apply_button = button("4. 画像変換を適用", self.apply_candidate,
            "確認済みの面間距離とcam1画像変換を撮影・解析タブへ適用し、位相回復を有効にします。適用前は撮影タブのキャリブレーションを置き換えません。")
        controls.addWidget(self.apply_button)
        controls.addStretch()
        self.load_cal_button = button("補正データを読込…", self.load_calibration,
            "以前保存した面間距離と変換マップを読み込みます。ベクトルと条件を確認し、「画像変換を適用」を押して再利用してください。")
        self.save_cal_button = button("補正データを保存…", self.save_calibration,
            "焦点位置・面間距離・float座標変換マップ・残差ベクトル・光学条件をNPZへ保存します。別の起動時にも読み込んで使えます。")
        controls.addWidget(self.load_cal_button)
        controls.addWidget(self.save_cal_button)
        split.addWidget(scroll)
        self.pages = QTabWidget()
        tip(self.pages, "入力画像、焦点合わせ、ベクトル、補正結果を切り替えます。計算結果はページを切り替えても保持します。")
        inputs = QSplitter(Qt.Orientation.Horizontal)
        for panel in self.camera_panels:
            inputs.addWidget(panel)
        self.pages.addTab(inputs, "入力画像")
        focus = QSplitter(Qt.Orientation.Vertical)
        for i in range(2):
            viewer = DepthViewer(f"cam{i} 焦点像", layout_mode="beside")
            self.bind_viewer(viewer)
            viewer.depthChanged.connect(self.focus_changed)
            focus.addWidget(viewer)
        focus_scroll = QScrollArea()
        focus_scroll.setWidgetResizable(True)
        focus_scroll.setWidget(focus)
        self.pages.addTab(focus_scroll, "焦点合わせ")
        self.vectors_before = VectorPlot("補正前 · cam0 → cam1 の変位")
        self.vectors_after = VectorPlot("補正後 · 残差ベクトル")
        vectors = QSplitter(Qt.Orientation.Horizontal)
        vectors.addWidget(self.vectors_before)
        vectors.addWidget(self.vectors_after)
        self.pages.addTab(vectors, "ベクトルマップ")
        corrected = QSplitter(Qt.Orientation.Horizontal)
        self.reference_panel = ImagePanel("cam0 · 元画像")
        self.corrected_panel = ImagePanel("cam1 · 変換後の元画像")
        corrected.addWidget(self.reference_panel)
        corrected.addWidget(self.corrected_panel)
        self.pages.addTab(corrected, "補正結果")
        split.addWidget(self.pages)
        split.setStretchFactor(1, 1)
        split.setSizes([290, 1150])

    def clear_results(self):
        super().clear_results()
        self.clear_candidate()
        self.update_gap()

    def clear_candidate(self):
        self.candidate = None
        self.candidate_source = None
        self.focus_signature = None
        self.vectors_before.set_vectors([], (1, 1))
        self.vectors_after.set_vectors([], (1, 1))
        self.reference_panel.view.clear()
        self.corrected_panel.view.clear()
        self.quality_label.setText("ベクトルマップは未計算です。")

    def focus_depths(self):
        if any(v.rendered is None or v.pending_depth is not None for v in self.viewers):
            return None
        return tuple(v.rendered.z_mm for v in self.viewers)

    def focus_selection(self):
        values = self.focus_depths()
        return (values, tuple(v.filtered.isChecked() for v in self.viewers)) if values else None

    def update_gap(self):
        values = self.focus_depths()
        if values is None:
            self.gap_label.setText("cam0 焦点: —\ncam1 焦点: —\n面間距離 Δz: —")
        else:
            gap = values[0]-values[1]
            self.gap_label.setText(f"cam0 焦点: {values[0]:.4f} mm\ncam1 焦点: {values[1]:.4f} mm\nΔz = {gap:+.4f} mm · |Δz| = {abs(gap):.4f} mm")

    def focus_changed(self, *_):
        if self.focus_signature is not None and self.focus_selection() != self.focus_signature:
            self.clear_candidate()
        self.update_gap()
        self.main.update_controls()

    def update_controls(self):
        super().update_controls()
        busy = self.main.worker is not None
        both = self.pair is not None and all(f is not None for f in self.pair.frames)
        self.scan_button.setEnabled(both and not busy)
        values = self.focus_depths()
        self.map_button.setEnabled(both and not busy and values is not None and abs(values[0]-values[1]) >= 1e-6)
        self.apply_button.setEnabled(self.candidate is not None and not busy)
        self.save_cal_button.setEnabled(self.candidate is not None and not busy)
        self.load_cal_button.setEnabled(not busy)
        self.iterations.setEnabled(not busy)

    def scan_focus(self):
        if self.main.worker is not None or self.pair is None:
            return
        try:
            self.pair.require_both()
            scans = [depths(lo.value(), hi.value(), self.step.value()) for lo, hi in zip(self.minimum, self.maximum)]
            config, pair = self.scan_config(), self.pair
            self.clear_results()
            self.pages.setCurrentIndex(1)
            def operation(cancel, progress):
                completed = []
                for camera in range(2):
                    try:
                        def camera_progress(stage, current, total, row):
                            progress(f"cam{camera} · {stage}", current, total, {'camera': camera, 'row': row})
                        result = analyze(pair, f"gabor_cam{camera}", config, None, 1, scans[camera], cancel, camera_progress)
                    except Cancelled:
                        if completed:
                            return completed
                        raise
                    completed.append(result)
                    progress(f"cam{camera} · 焦点探索完了", 1, 1, {'camera': camera, 'analysis': result})
                    if result.stopped:
                        break
                return completed
            self.main.submit(self, "焦点探索", operation, self.focus_done)
        except Exception as exc:
            self.main.notify(str(exc))

    def on_progress(self, stage, current, total, data):
        if not isinstance(data, dict):
            return
        viewer = self.viewers[data['camera']]
        if 'analysis' in data:
            viewer.set_analysis(data['analysis'])
        elif data.get('row') is not None:
            viewer.plot.rows.append(data['row'])
            viewer.plot.update()

    def focus_done(self, results):
        self.update_gap()
        self.main.notify("各カメラの山型ピークと画像を確認し、必要なら深度を微調整してください。続いてベクトルマップを計算します。" if len(results) == 2 else
                         "探索を中断しました。計算できたカメラ・深度の結果は保持しています。")

    def build_map(self):
        values = self.focus_depths()
        if values is None or self.pair is None or self.main.worker is not None:
            return
        try:
            separation_from_focus(values)
            pair = self.pair
            config = replace(self.main.config, gs_iterations=self.iterations.value())
            # Keep the selected renders alive until fitting has finished.
            selection = self.focus_selection()
            focused = [v.rendered.filtered if v.filtered.isChecked() else v.rendered.unfiltered for v in self.viewers]
            self.clear_candidate()
            def done(result):
                if self.focus_selection() != selection:
                    self.main.notify("計算中に焦点位置が変わったため、ベクトルマップを再計算してください。")
                    return
                result.calibration.metadata['focus_filtered'] = list(selection[1])
                self.candidate_ready(result, selection)
            self.main.submit(self, "ベクトルマップ", lambda c, p: build_from_focused(pair, config, values, focused, c, p),
                done)
        except Exception as exc:
            self.main.notify(str(exc))

    def candidate_ready(self, result, focus_signature=None, source=None):
        self.candidate = result.calibration if isinstance(result, CalibrationPreview) else result
        self.candidate_source = source
        self.focus_signature = focus_signature or self.focus_selection()
        m = self.candidate.metadata
        shape = self.candidate.map_x.shape
        self.vectors_before.set_vectors(m.get('vectors_before', []), shape)
        self.vectors_after.set_vectors(m.get('vectors_after', []), shape)
        if isinstance(result, CalibrationPreview):
            self.reference_panel.view.set_image(self.pair.frames[0].image)
            self.corrected_panel.view.set_image(result.corrected_raw)
        else:
            self.reference_panel.view.clear()
            self.corrected_panel.view.clear()
        gap = m.get('plane_separation_mm')
        if gap is None and m.get('focus_depths_mm'):
            gap = separation_from_focus(m['focus_depths_mm'])
        self.quality_label.setText(f"{m.get('inliers', '?')} 対応点 · 適用前\nフィット RMS {m.get('rms_px', 0):.3f} px\n"
            f"検証 RMS {m.get('holdout_rms_px', 0):.3f} px\n補正後 RMS {m.get('post_rms_px', float('nan')):.3f} px\n"
            + (f"Δz = {gap:+.4f} mm" if gap is not None else "面間距離がありません")
            + (f"\n読込: {source}" if source else ""))
        if source and m.get('focus_depths_mm'):
            z0, z1 = m['focus_depths_mm']
            self.gap_label.setText(f"保存時の焦点: {z0:.4f} / {z1:.4f} mm\nΔz = {gap:+.4f} mm")
        if source and m.get('gs_iterations'):
            self.iterations.setValue(int(m['gs_iterations']))
        self.pages.setCurrentIndex(2)
        self.main.notify("補正前後のベクトルと補正結果を確認し、「画像変換を適用」を押してください。")
        self.main.update_controls()

    def apply_candidate(self):
        if self.candidate is None:
            return
        try:
            calibration = self.candidate
            m = calibration.metadata
            gap = m.get('plane_separation_mm')
            if gap is None:
                gap = separation_from_focus(m.get('focus_depths_mm', ()))
            config = replace(self.main.config, plane_separation_mm=float(gap), gs_iterations=self.iterations.value(),
                             calibration_file=self.candidate_source).validate()
            for key in ('pixel_pitch_um', 'wavelength_nm'):
                if not np.isclose(m.get(key, 0), getattr(config, key), rtol=1e-8, atol=0):
                    raise ValueError(f"補正データの {key} と現在の設定が一致しません。設定を合わせてください。")
            if m.get('padding_size', 4096) != config.padding_size:
                raise ValueError("補正データのパディングと現在の設定が一致しません。")
            if m.get('serials') != [config.serial0, config.serial1]:
                raise ValueError("補正データのカメラ番号・シリアルが現在の設定と一致しません。")
            # A loaded map is checked against its own geometry; acquisition input
            # compatibility is checked again before enabling GS.
            if not m.get('quality_passed') or not np.any(calibration.calibrated_mask):
                raise ValueError("品質検証済みの有効な変換マップが必要です。")
            self.main.acquisition.clear_results()
            calibration.metadata['gs_iterations'] = self.iterations.value()
            self.main.config, self.main.calibration = config, calibration
            self.main.acquisition.iterations.setValue(config.gs_iterations)
            self.quality_label.setText(self.quality_label.text().replace("適用前", "適用済み"))
            self.main.notify(f"画像変換と面間距離 Δz = {gap:+.4f} mm を適用しました。撮影・解析タブで位相回復を選べます。")
            self.main.update_controls()
        except Exception as exc:
            self.main.notify(str(exc))

    def load_calibration(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "補正データを読込", "", "補正データ (*.npz)")
        if path:
            path = str(Path(path).resolve())
            pair, config = self.pair, self.main.config
            def operation(cancel, progress):
                calibration = Calibration.load(path)
                if pair is not None:
                    try:
                        calibration.validate_for(pair, replace(config, plane_separation_mm=None))
                    except ValueError:
                        return calibration
                    return CalibrationPreview(calibration, calibration.apply(pair.frames[1].image), None)
                return calibration
            self.main.submit(self, "補正データ読込", operation,
                lambda result: self.candidate_ready(result, source=path))

    def save_calibration(self):
        if self.candidate is None:
            return
        default = self.main.session.path / "calibration.npz"
        try:
            self.main.session.ensure(self.main.config)
        except Exception as exc:
            self.main.notify(str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, "補正データを保存", str(default), "補正データ (*.npz)")
        if path:
            if not Path(path).suffix:
                path += ".npz"
            calibration = self.candidate
            def done(result):
                self.candidate_source = str(Path(path).resolve())
                if self.main.calibration is calibration:
                    self.main.config = replace(self.main.config, calibration_file=self.candidate_source)
                self.main.notify(f"補正データを保存しました: {path}")
            self.main.submit(self, "補正データ保存", lambda c, p: calibration.save(path), done)
