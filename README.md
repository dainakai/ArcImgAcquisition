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
このリポジトリは撮影アプリのみを管理し、撮影データ、解析結果、SDK、ビルド出力は含めません。
