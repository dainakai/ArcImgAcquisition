# DualHolo Analyze v0.1.0

ホログラムのCaptureとCPU再構成を行う独立アプリの初回リリースです。
連続撮影用のDualHoloも引き続き使用できます。
このリリースはDualHoloを置き換えず、インストール先とアプリ名、設定ファイル、タグを分けています。

| ファイル名の末尾 | 対象 |
|---|---|
| `windows-x64.zip` | Windows x64 |
| `ubuntu-x64.tar.gz` | Ubuntu x64 |
| `macos-arm64.zip` | Apple Silicon Mac |
| `macos-x64.zip` | Intel Mac |

アーカイブを展開し、`DualHoloAnalyze` アプリを起動してください。
Pythonの別途インストールは不要です。
カメラとSDKがなくても起動でき、`Simulate 10 Hz` と保存済み画像の読込みを使用できます。
実カメラには別途Spinnaker SDKとドライバが必要です。

- 同期した2画像のCaptureとResume。
- Gabor再生、2面のGS位相回復、Tamuraによる焦点探索と途中停止。
- 深度スライダーと数値入力による再生、帯域制限の有無の切替。
- ガラスプレートからのサブピクセル位置合わせとキャリブレーションの保存、読込み。
- 画像のズームとスクロール、クリップボードコピー、TIFFまたはPNG保存。
- 4096×4096の平均値パディング、CPU計算、YAMLによる光学条件設定。

位相回復には、画像に適合するキャリブレーションと、`config.yaml` の符号付きカメラ間距離 `plane_separation_mm` の設定が必要です。
カメラとストリームの設定は変更しません。
DualHoloとAnalyzeは同じ排他ロックを使い、実カメラを同時に開きません。

各OSで数値テスト、GUIテスト、配布アプリのカメラなし起動、模擬入力、同梱した取得ライブラリの模擬動作を検証して公開します。
実カメラの動作はこのCIの検証範囲外です。
macOSのDeveloper ID署名と公証、WindowsのAuthenticode署名はありません。

操作と設定は同梱のREADMEを参照してください。
SHA-256は各アーカイブと同名の `.sha256` にあります。
