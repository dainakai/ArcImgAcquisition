# DualHolo Analyze

2台のホログラムを固定して、Gabor再生、Tamura焦点探索、ガラスプレートによる位置合わせ、2面のGS位相回復を行うCPU用GUIです。
Windows、macOS、Ubuntu向けにPythonとQtで実装しています。
カメラやSpinnaker SDKがなくても起動でき、模擬入力と保存済み画像を使用できます。
このアプリはユーザー指定による例外として、手元のCPUで再構成し、ローカルへ結果を保存します。

配布アプリは [DualHolo Analyze v0.1.0](https://github.com/dainakai/ArcImgAcquisition/releases/tag/analyzer-v0.1.0) から取得できます。
Analyzeは `analyzer-v*`、連続撮影用のDualHoloは `v*` のタグで別々にリリースします。
両方のアプリを同じPCへ配置でき、Analyzeの利用にDualHolo本体のインストールは不要です。

## カメラなしで起動する

Python 3.12の仮想環境を作成し、リポジトリのルートで次を実行します。

```sh
python3 -m venv 260909/analyzer/.venv
260909/analyzer/.venv/bin/python -m pip install -r 260909/analyzer/requirements.txt
260909/analyzer/.venv/bin/python 260909/analyzer/run.py --simulate
```

Windows PowerShellでは次を実行します。

```powershell
py -3.12 -m venv 260909/analyzer/.venv
260909/analyzer/.venv/Scripts/python -m pip install -r 260909/analyzer/requirements.txt
260909/analyzer/.venv/Scripts/python 260909/analyzer/run.py --simulate
```

`--simulate` を省略すると、未接続のGUIを開きます。
`Load image / pair…` から保存画像を選択できます。
`--config /path/to/config.yaml` で設定を指定でき、相対パスは設定ファイルの場所を基準に解釈します。
セットアップ後はmacOSとUbuntuで `run.command`、Windowsで `Run.cmd` も使用できます。

## Captureと画像の読み込み

`Simulate 10 Hz` または `Connect cameras` で2台の画像を左右に表示します。
`Capture` は最新の同期ペアをメモリ内に固定し、`Resume` は固定画像と解析結果を破棄してライブ表示へ戻ります。
新しいペアを300 ms以上受信していない場合はCaptureを無効にします。
Captureそのものではディスクへ画像を保存しません。

`Load image / pair…` はDualHoloの新旧の保存構造に対応します。
現在の `recording_…/cam0_…/frame_….tiff` 形式では、`frames.csv` の64 bit整数の推定露光時刻を照合して相手を選びます。
同じ連番やフレームIDでも同じ露光とは限らないため、それらを対応づけの根拠にしません。
旧形式の `frame_…/cam0_….tiff` では同じペアフォルダのもう一方を選択します。
対応する画像が一意に決まらない場合は、片側だけを読み込み、理由を画面下部へ表示します。
`Load cam1…`（cam0が欠けている場合は `Load cam0…`）で手動指定できます。

Recは起動時OFFです。
RecボタンまたはRで明示的に開始した区間だけ、受信した全画像をDualHoloと同じRecorderで保存します。
Capture中も、明示的に開始したRecは継続します。
Nは表示コントラスト、QとEscは終了です。
終了時は計算を停止し、保存待ちの録画画像を書き終えてからカメラを閉じます。

## 再生と焦点探索

画像をCaptureまたは読込み後、`Gabor · cam0`、`Gabor · cam1`、`Phase recovery · 2 cameras` のいずれかを選びます。
最小深度、最大深度、間隔を入力し、`Analyze` を押します。
位相回復モードでは、指定回数のGSを実行してから焦点探索へ進みます。

深度ごとに再構成強度の **Tamuraコントラスト** `std(I) / mean(I)` を計算し、曲線を逐次更新します。
これは既存の解析スクリプトと同じ定義です。
完了時は表示中のフィルタ条件でTamuraが最大となる計算済み深度を表示します。
最大深度は指定間隔の格子に乗る場合だけ含め、最大値に合わせて間隔を変えません。
最大値が探索端にある場合は画面下部で通知します。
z=0のセンサ像が最大となる場合もあり、Tamura最大値だけで物体の実深度を保証するものではありません。

`Stop` はFFTの前後と小さな計算ブロックの間で中断します。
実行中の1回のFFTは完了を待ちます。
焦点探索を中断した場合は完了済みの曲線と最大位置を保持し、partial scanと表示します。
GSが終わる前に中断した場合、未完了の位相を解析結果として採用しません。

探索後はDepthへ値を入力してEnterを押すか、スライダーを動かすと、その位置を自動再生します。
スライダーの連続操作は既定160 msにまとめ、古い要求を取り消して最新の位置を計算します。
`2D anti-alias filter` は同じ深度の計算済み画像を切り替えます。
表示コントラストの範囲も2画像で共通にし、切替時に再計算しません。
複素スペクトル1枚と容量制限付きの画像キャッシュを保持し、深度ごとの画像スタックを保存しません。

再構成画像はホイールと＋／−で拡大縮小し、ドラッグまたは縦横のスクロールバーで移動できます。
Fitは画面に合わせ、1:1は画像の1画素を表示の1画素に戻します。
`Copy image` は表示コントラストの画像全体をクリップボードへコピーします。
`Save image as…` のTIFFはfloat32強度、PNGは表示コントラストの8 bit画像です。
同名のJSONへ使用条件、入力ハッシュ、キャリブレーション情報を記録し、CSVへTamura曲線を保存します。

## 光学設定

設定の例は [config.yaml](config.yaml) にあります。
GUIの `Load YAML config…` と `Save YAML config…` でも読み書きできます。
設定の読込み前にはカメラをDisconnectし、計算を停止します。

| キー | 単位と意味 | 既定値 |
|---|---|---|
| `wavelength_nm` | 波長、nm | 515 |
| `pixel_pitch_um` | 画素ピッチ、µm | 2.74 |
| `plane_separation_mm` | 符号付きのcam0からcam1への伝搬距離、mm | null |
| `scan_min_mm` / `scan_max_mm` / `scan_step_mm` | 焦点探索の範囲と間隔、mm | 30 / 90 / 1 |
| `gs_iterations` | GSの往復回数 | 20 |
| `calibration_file` | 保存済みキャリブレーションのパス | null |
| `output_dir` | 保存先 | captures |
| `cache_megabytes` | 深度画像キャッシュの上限、MiB | 192 |
| `slider_debounce_ms` | 連続スライダー操作をまとめる時間、ms | 160 |

伝搬は `exp(+i 2π z sqrt(λ⁻² − fx² − fy²))` の符号規約です。
位相回復後の深度もcam0面からの符号付き距離です。
cam0とcam1の番号は光学的な前後関係を表さないため、`plane_separation_mm` は装置に合わせて設定します。
既定値nullのままでは位相回復を許可しません。
強度に影響しない一様な位相は、計算精度を保つため伝達関数から除いています。

すべての光伝搬は元の画素ピッチの4096×4096配列で行います。
Gaborの入力は `sqrt(I)` で、周囲を振幅の平均で埋めます。
複素場は複素平均で埋め、GSの往復時にも画像領域を取り出してから同じ規則で埋め直します。
表示と評価は中央の元画像領域だけを使います。
4096を超える画像はエラーとし、自動縮小しません。
共通実装は `../registration/optical_padding.py` と `optical_bandlimit.py` です。

フィルタは各方向の `q_j = 2|z||f_j| / (L_j sqrt(λ⁻² − fx² − fy²))` を使います。
`1 − 10⁻⁶` を安全側の端とし、帯域内の端5%をcosineで減衰させます。
GSの各往復には常にフィルタを適用し、「なし」の表示は得られた同じ位相場からの後段伝搬だけを比較します。
この帯域制限は伝達関数のサンプリングを対象とし、撮影範囲外の縞や周期境界誤差全般の解消を保証しません。

## ガラスプレートによるキャリブレーション

ガラスプレートを撮影してCaptureするか、その画像ペアを読み込み、`Calibrate captured glass…` を押します。
各カメラの焦点を現在の探索範囲から求める方法と、Gaborで確認した焦点位置を直接入力する方法を選べます。
自動探索の最大位置が範囲端ならキャリブレーションを採用せず、範囲の修正を求めます。

焦点を合わせた2画像から局所相関のサブピクセル変位を求め、往復対応の確認後に2次多項式をロバストに当てはめます。
対応点数、残差、空間的に分けた検証点の誤差が設定された基準を満たした場合だけ、float32の座標マップを作成します。
この誤差は画像上の対応の検証値であり、独立な物理計測による絶対精度ではありません。
マップの方向は「cam0の出力座標からcam1の元画像を読む座標」です。

`Save calibration…` はマップ、支持領域、光学条件、カメラシリアル、入力画像ハッシュをNPZへ保存します。
次回は `Load calibration…` またはYAMLの `calibration_file` で再利用できます。
画像サイズ、カメラ順序、波長、画素ピッチが異なるキャリブレーションでは位相回復を無効にします。
装置の配置やROIを変更した場合は、新しいガラスプレート画像で再較正してください。
旧研究用NPZには必要なメタデータがないため、このアプリで作成した形式を使います。

GSでは元のcam1画像にLanczos4補間を一度だけ適用します。
測定点の支持領域と補間カーネルの有効範囲の共通部分だけにcam1の振幅拘束を適用し、未較正領域は推定値を保持します。
その領域の平均強度でカメラ間の光量を合わせ、補間で生じた負値は振幅化の直前に0へ制限します。
保存済みcam0画像を追加で上下反転する処理はありません。

## セッションと保存先

通常はアプリを開いている間、1つのセッションを使います。
カメラをDisconnectして `New session` を押すと新しいセッションへ切り替えます。
保存先の基本構造はDualHoloに合わせています。

```text
captures/session_YYYYMMDD_HHMMSS_xxxxxx/
  config.yaml
  camera0.yml                     # 実カメラ接続時の読み取り値
  camera1.yml
  recording_YYYYMMDD_HHMMSS_xxxxxx/
    frames.csv                    # RecまたはSave captured raw pair
    cam0_26259157/frame_000000_id….tiff
    cam1_26259158/frame_000000_id….tiff
  recording_<Captureした日時>/
    reconstructions/
      gabor_cam0_z+50.000000mm_filtered.tiff
      gabor_cam0_z+50.000000mm_filtered.tiff.json
      gabor_cam0_z+50.000000mm_filtered.tiff.csv
```

`Save captured raw pair` は固定中の生画像を保存します。
生画像にはコントラスト調整や幾何補正を適用しません。
再構成画像の保存先と名前はダイアログで変更できます。
配布アプリは隣のconfig.yamlを優先し、macOSで `.app` だけを移動した場合は内蔵設定を使い、ユーザーのPictures内の `DualHoloAnalyze/captures` を保存先にします。

## 実カメラ用の取得ライブラリ

カメラ用C APIブリッジはDualHoloの `Cameras`、`Pairer`、`Recorder`、`CameraLock` を共有します。
カメラとストリームの設定は変更せず、既存の外部10 Hzトリガを使用します。
DualHoloとこのアプリは同じ排他ロックを取り、二重にカメラを開きません。
Spinnakerを開くのは `Connect cameras` を押したときだけです。

```sh
cmake -S 260909 -B 260909/build -DHOLO_ANALYZER_BRIDGE=ON -DCMAKE_BUILD_TYPE=Release
cmake --build 260909/build --target holo_capture --parallel 4
```

生成物はmacOSで `260909/build/analyzer/libholo_capture.dylib`、Ubuntuで `libholo_capture.so`、WindowsのVisual Studio構成で `analyzer/Release/holo_capture.dll` です。
別の場所へビルドした場合はYAMLの `camera_library` または環境変数 `HOLO_CAPTURE_LIBRARY` に絶対パスを指定します。
Spinnakerの導入方法は [DualHoloの配布説明](../docs/distribution.md) と共通です。
ライブラリがなくてもGUI、模擬入力、ファイル解析は使用できます。

GUIのほかに、計算1、実カメラ取得2、録画書込み1の最大4ワーカーを使います。
FFT、BLAS、OpenCVの内部並列数は1に制限し、GPUは使いません。
設定変更、入力変更、Resumeで前の解析結果とキャッシュを無効にします。

## 検証と配布物の作成

```sh
260909/analyzer/.venv/bin/python -m pip install pytest pyinstaller
260909/analyzer/.venv/bin/python -m pytest 260909/analyzer/tests --run-optical -q
```

`--run-optical` は4096角の数値検証を有効にします。
省略時は光伝搬を含むテストをスキップします。
`HOLO_CAPTURE_LIBRARY` を設定すると、ネイティブ取得ブリッジの模擬入力とRecも検証します。
実カメラを開くテストはありません。

配布物には各OS上でビルドした共有ライブラリを同梱します。
OpenCVを静的に組み込んでから、次のようにPyInstallerで作成します。

```sh
cmake -S 260909 -B 260909/build-analyzer -DHOLO_ANALYZER_BRIDGE=ON -DHOLO_BUNDLED_OPENCV=ON -DCMAKE_BUILD_TYPE=Release
cmake --build 260909/build-analyzer --target holo_capture --config Release --parallel 4
260909/analyzer/.venv/bin/python 260909/analyzer/package.py \
  --bridge 260909/build-analyzer/analyzer/libholo_capture.dylib \
  --native-licenses 260909/build-analyzer/third-party-licenses
```

`--bridge` は対象OSのファイル名へ置き換えます。
macOS版は `.app`、Windows版は `.exe` を含むフォルダ、Ubuntu版は実行ファイルを含むフォルダです。
パッケージ生成時にカメラなし起動と模擬入力を検証し、依存関係のバージョン、ライセンス、アーカイブのSHA-256を同梱します。
[専用GitHub Actions](../../.github/workflows/analyzer.yml) はWindows x64、macOS arm64とx64、Ubuntu x64で数値テストと配布物作成を行う設定です。
`analyzer-v*` タグでは4種類のテストがすべて成功してから、同じタグのAnalyze専用GitHub Releaseへ公開します。
公開時に既存のDualHoloのLatest指定を変更しません。
通常のDualHoloビルドでは `HOLO_ANALYZER_BRIDGE` はOFFで、Analyze用の取得ライブラリは作成しません。
各OSの実カメラとドライバを用いた動作確認は別途必要です。
Qtの配布方法と対象環境は [Qt for Pythonの配布説明](https://doc.qt.io/qtforpython-6/deployment/index.html) と [対応プラットフォーム](https://doc.qt.io/qt-6/supported-platforms.html) を参照してください。
