# ArcImgAcquisition / DualHolo

外部10 Hzトリガで2台のSpinnakerカメラを表示し、Rec中に受信した全画像を無圧縮TIFFへ保存するC++17アプリです。
カメラ設定は読み取り専用で、起動時はRec OFFです。

[配布アプリ](https://github.com/dainakai/ArcImgAcquisition/releases) / [ビルドと配布手順](260909/docs/distribution.md) / [操作と保存仕様](260909/README.md)

Windows x64、Linux x64、macOS arm64とx64をGitHub Actionsでビルドします。
`v*`タグのテスト成功後にReleaseへ配布アーカイブを登録します。
実カメラにはSpinnakerランタイムとドライバが必要で、模擬入力はSDKなしで動作します。

```sh
cmake -S 260909 -B 260909/build -DCMAKE_BUILD_TYPE=Release
cmake --build 260909/build --parallel 4
ctest --test-dir 260909/build --output-on-failure
```

ソースは `260909/src`、テストは `260909/tests` にあります。
同じリポジトリの [DualHolo Analyze](260909/analyzer/README.md) は、2台の画像のCapture、Gabor再生、Tamura焦点探索、キャリブレーションとGS位相回復に対応するCPU用GUIです。
連続撮影用のDualHoloとは別アプリです。[Analyzeの配布](https://github.com/dainakai/ArcImgAcquisition/releases/tag/analyzer-v0.1.0) は `analyzer-v*` タグで管理します。
カメラとSDKがなくても起動でき、保存済みのDualHolo画像を読み込めます。
ソースと配布用CIを `260909/analyzer` に置き、撮影データ、解析結果、SDK、ビルド出力はGitに含めません。
