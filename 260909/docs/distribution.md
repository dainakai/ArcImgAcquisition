# ビルドと配布

## 配布アプリの起動

[Releases](https://github.com/dainakai/ArcImgAcquisition/releases) からOSに合うアーカイブを取得し、書き込み可能な場所へ展開します。
WindowsとLinuxは設定ファイルとアプリを同じ配布フォルダに置いて使います。
macOSの `.app` には既定の設定も含まれるため、アプリだけを移動しても起動できます。

| 配布名 | 対象 | 起動 |
|---|---|---|
| `windows-x64.zip` | Windows 10/11 x64 | `Run.cmd` または `dual_holo.exe` |
| `linux-x64.tar.gz` | Ubuntu 22.04以降のx64 | `./run.sh` |
| `macos-arm64.zip` | macOS 13以降、Apple Silicon | `DualHolo.app` |
| `macos-x64.zip` | macOS 13以降、Intel | `DualHolo.app` |

ファイル名にはアプリ名とバージョンも付きます。
OpenCVは静的に組み込み、Windows版はC/C++ランタイムも静的リンクします。
Linux版はGTK 3などのOSライブラリを使います。
Ubuntu 22.04では `sudo apt install libgtk-3-0`、Ubuntu 24.04では `sudo apt install libgtk-3-0t64` でGUIの実行環境を用意できます。
それ以外のLinuxディストリビューションはソースからのビルドを使ってください。
macOS版はアドホック署名のみで、Appleの公証とDeveloper ID署名はありません。
Windows版にもAuthenticode署名はありません。
OSが起動を確認する場合は、配布元を確認してOSの許可操作を行います。

macOSの既定の保存先は `DualHolo.app` と同じフォルダの `captures` です。
Finderから起動した場合も、ターミナルの作業フォルダに関係なくこの場所へ保存します。
アプリの隣に書き込めない場合はエラーを表示します。別の保存先へ自動変更しません。
その場合はFinderでアプリを書き込み可能なフォルダへ移動して起動し直すか、`--output` で保存先を指定してください。
SDKの不足などで起動できない場合は、エラーをダイアログに表示します。
独自の設定を確実に指定する場合は、ターミナルから `--config /設定ファイルの絶対パス/config.yml` で起動してください。
`--config` を明示した場合、相対指定の `output_dir` は従来どおり設定ファイルの場所を基準にします。
`--output /保存先の絶対パス` で保存先だけを指定することもできます。

WindowsはZIPを「すべて展開」してから、まず `Simulate.cmd` でGUIを確認します。
これはSDKとカメラがなくても動きます。
実カメラ用の `Run.cmd` は、失敗した際にコンソールを残します。
`dual_holo.exe` のダブルクリック起動で失敗した場合も、エラーダイアログが残ります。
詳細ログは `%LOCALAPPDATA%\DualHolo\logs\startup-error-*.txt` に保存され、ダイアログにもログの場所が表示されます。
ログには実行ファイルの場所、作業フォルダ、SDKの探索先やWindowsのエラー理由を残します。
問い合わせ時は表示されたエラー、またはこのログを確認してください。
Windowsの既定の保存先は、ユーザーの「ピクチャ」フォルダ内の `DualHolo\captures` です。
`--config` を明示した場合の相対パスと、`--output` による上書きはmacOSと同じです。

実カメラの使用には、同じCPUアーキテクチャに対応する[Spinnaker SDK/runtime](https://www.teledynevisionsolutions.com/products/spinnaker-sdk/)とカメラドライバが必要です。
C APIライブラリを含むSpinnaker 4.xを想定しています。
SDK側の対応OSも確認してください。特にIntel Macは対応するSDKの入手が必要です。
SDKとドライバはこのアーカイブに同梱しません。

Windowsでは `C:\Program Files\Teledyne\Spinnaker\bin64\vs2015\SpinnakerC_v140.dll` を最初に探索します。
`ProgramFiles` が別ドライブを指す場合は、その配下を使います。
従来のインストール先・DLL名も探索します。
標準外の場所へインストールした場合は、環境変数 `SPINNAKER_C_LIBRARY` にC APIライブラリの絶対パスを指定します。
依存するSDKライブラリとGenTLも公式インストーラで導入してください。

```powershell
# Windows PowerShellの例。実際のSDKインストール先に合わせる。
$env:SPINNAKER_C_LIBRARY = 'C:\Program Files\Teledyne\Spinnaker\bin64\vs2015\SpinnakerC_v140.dll'
.\dual_holo.exe --config config.yml
```

```sh
# Linuxの例
SPINNAKER_C_LIBRARY=/opt/spinnaker/lib/libSpinnaker_C.so ./dual_holo --config config.yml
# macOSの例
SPINNAKER_C_LIBRARY=/Applications/Spinnaker/lib/libSpinnaker_C.dylib ./DualHolo.app/Contents/MacOS/DualHolo --config config.yml
```

`inspect_cameras --runtime-only` はライブラリと関数の解決だけを確認し、カメラを開きません。
引数なしの `inspect_cameras` はカメラを開いて現在の設定を読みます。
撮影アプリと同じ排他ロックを使用します。

まずSDKが不要な模擬入力で確認します。
Windowsは `Simulate.cmd`、macOSは `Simulate.command`、Linuxは `./run.sh --simulate` で起動できます。
実カメラの使用前にSpinViewなどを閉じ、外部10 Hzトリガとカメラ設定を準備します。
アプリは設定値を変更せず、起動時はRec OFFです。

## ソースからのビルド

C++17コンパイラ、CMake 3.24以降、OpenCVのcore/imgproc/imgcodecs/highguiが必要です。
テストにはPython 3を使います。
Spinnakerのヘッダとリンクライブラリはビルド時に必要ありません。

```sh
# Ubuntu 22.04/24.04 x64
sudo apt install build-essential cmake libopencv-dev python3
cmake -S 260909 -B 260909/build-linux -DCMAKE_BUILD_TYPE=Release
cmake --build 260909/build-linux --parallel 4
ctest --test-dir 260909/build-linux --output-on-failure
./260909/build-linux/dual_holo --config 260909/config.yml --simulate
```

```sh
# macOS: Homebrewでcmake/opencvを導入後
cmake -S 260909 -B 260909/build-mac -DCMAKE_BUILD_TYPE=Release
cmake --build 260909/build-mac --parallel 4
ctest --test-dir 260909/build-mac --output-on-failure
./260909/build-mac/DualHolo.app/Contents/MacOS/DualHolo --config 260909/config.yml --simulate
```

WindowsではVisual Studio 2022の「C++によるデスクトップ開発」、CMake、Python 3を用意します。
次のコマンドはOpenCVもビルドします。
初回は公式GitHubから固定したOpenCV 4.12.0のソースを取得します。

```powershell
cmake -S 260909 -B 260909/build-win -G 'Visual Studio 17 2022' -A x64 -DHOLO_BUNDLED_OPENCV=ON
cmake --build 260909/build-win --config Release --parallel 4
ctest --test-dir 260909/build-win -C Release --output-on-failure
.\260909\build-win\Release\dual_holo.exe --config 260909/config.yml --simulate
```

## CIとRelease

`main`へのpush、pull request、手動実行でWindows x64、Linux x64、macOS arm64、macOS x64をビルドします。
CIは実カメラを使わず、録画、画素値保存、排他ロック、模擬入力を検証します。
配布フォルダと展開後のアプリでも模擬録画を実行します。
GUIは指定時間動き続けて複数回描画したことを確認し、正常な終了コードだけで合格にしません。
LinuxではXvfbとOpenboxを使い、ウィンドウの表示、Rでの録画開始、タイトルバーの「×」による終了と保存待ち画像の書き出しも検証します。
macOSでは設定ファイルを隣に置かず、書き込み可能なフォルダに読み取り専用の `.app` だけを配置して、Finderと同じ起動経路も検証します。
この検証では `--config` と `--output` を指定せず、内蔵設定の読み込み、アプリの隣の `captures`、GUIの継続描画を確認します。
隣に書き込めない場合のエラーと、`--output` による保存先の指定も検証します。
Windowsでは模擬DLLをTeledyneのインストール先と同じフォルダ構成に配置し、環境変数でDLLを指定しなくても読み込めることを確認します。
Windowsでは配布された `Simulate.cmd` と `Run.cmd --simulate` からGUIを表示し、保存先指定なしの起動も検証します。
SDKが見つからない場合と設定が不正な場合は、実カメラに触れずにエラーダイアログ、詳細ログ、失敗の終了コードまで確認します。
成果物にはSHA-256と組み込んだライブラリのライセンスを含めます。
Spinnakerランタイムと実カメラを使った各OSでの取得確認は、このCIの検証範囲外です。

```sh
# バージョンは260909/CMakeLists.txtで更新する。Info.plistにも自動反映される。
git tag v1.2.4
git push origin v1.2.4
```

`v*`タグの全ビルドとテストが成功すると、4種類のアーカイブをGitHub Releaseへ登録します。
`v1.2.0-rc.1`のようにハイフンを含むタグはPrereleaseになります。
通常のpushではActionsのArtifactsから取得できます。

ローカルで配布形式を検証する場合は、`HOLO_BUNDLED_OPENCV=ON`でビルドしてから次を実行します。
LinuxではOpenCVビルド用に `libgtk-3-dev`、配布物のGUIテスト用に `xvfb`、`xauth`、`openbox`、`xdotool`、`wmctrl` も必要です。

```sh
python3 260909/scripts/package.py --build 260909/build-release --platform linux-x64 --output 260909/dist
```
