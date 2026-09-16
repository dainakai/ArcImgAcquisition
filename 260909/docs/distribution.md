# ビルドと配布

## 配布アプリの起動

[Releases](https://github.com/dainakai/ArcImgAcquisition/releases) からOSに合うアーカイブを取得し、書き込み可能な場所へ展開します。
プライベートリポジトリのReleaseを取得できるのは、リポジトリへのアクセス権を持つユーザーです。
設定ファイルとアプリを同じ配布フォルダに置いて使います。

| 配布名 | 対象 | 起動 |
|---|---|---|
| `windows-x64.zip` | Windows 10/11 x64 | `dual_holo.exe` |
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

実カメラの使用には、同じCPUアーキテクチャに対応する[Spinnaker SDK/runtime](https://www.teledynevisionsolutions.com/products/spinnaker-sdk/)とカメラドライバが必要です。
C APIライブラリを含むSpinnaker 4.xを想定しています。
SDK側の対応OSも確認してください。特にIntel Macは対応するSDKの入手が必要です。
SDKとドライバはこのアーカイブに同梱しません。

アプリは標準インストール先を探索します。
標準外の場所へインストールした場合は、環境変数 `SPINNAKER_C_LIBRARY` にC APIライブラリの絶対パスを指定します。
依存するSDKライブラリとGenTLも公式インストーラで導入してください。

```powershell
# Windows PowerShellの例。実際のSDKインストール先に合わせる。
$env:SPINNAKER_C_LIBRARY = 'C:\Program Files\FLIR Systems\Spinnaker\bin64\vs2015\Spinnaker_C_v140.dll'
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
成果物にはSHA-256と組み込んだライブラリのライセンスを含めます。
Spinnakerランタイムと実カメラを使った各OSでの取得確認は、このCIの検証範囲外です。

```sh
# バージョンは260909/CMakeLists.txtとInfo.plistも更新する。
git tag v1.2.0
git push origin v1.2.0
```

`v*`タグの全ビルドとテストが成功すると、4種類のアーカイブをGitHub Releaseへ登録します。
`v1.2.0-rc.1`のようにハイフンを含むタグはPrereleaseになります。
通常のpushではActionsのArtifactsから取得できます。

ローカルで配布形式を検証する場合は、`HOLO_BUNDLED_OPENCV=ON`でビルドしてから次を実行します。
LinuxではOpenCVビルド用に `libgtk-3-dev` も必要です。

```sh
python3 260909/scripts/package.py --build 260909/build-release --platform linux-x64 --output 260909/dist
```
