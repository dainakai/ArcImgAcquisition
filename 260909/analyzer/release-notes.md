# DualHolo Analyze v0.6.0

- Gabor／PRのMinIP作成、閲覧、保存を追加。奥行き範囲と再構成間隔を指定できます。
- 再生画像とMinIPを8 bitグレースケールBMP／PNGへ保存。BMPを既定とし、PNGは圧縮レベル0で保存します。
- Q／Escで計算だけを中断するよう修正。アプリは×ボタンで終了します。
- キャリブレーションの既定値を相関窓256 px、局所探索半径32 pxへ変更。
- [ImageJでMinIPから粒径分布を得る手順](https://github.com/dainakai/ArcImgAcquisition#minipから粒径分布を得る方法)を、実データとスクリーンショット付きで追加。

| ファイル名の末尾 | 対象 |
|---|---|
| `windows-x64.zip` | Windows x64 |
| `ubuntu-x64.tar.gz` | Ubuntu x64 |
| `macos-arm64.zip` | Apple Silicon Mac |
| `macos-x64.zip` | Intel Mac |

展開して`DualHoloAnalyze`を起動してください。Pythonの別途インストールは不要です。
カメラやSDKなしで起動でき、「シミュレーション」と保存画像の解析を使用できます。
実カメラには別途Spinnaker SDKとドライバが必要です。
カメラ設定は変更せず、DualHoloと同じ排他ロックで重複接続を防ぎます。

各OSで4k・8kの数値検証、較正からGSまでのGUIテスト、配布版の未接続起動・模擬入力を検証します。実カメラはこの検証の対象外です。
macOSのDeveloper ID署名・公証とWindowsのAuthenticode署名はありません。
操作と設定は同梱README、SHA-256は各アーカイブと同名の`.sha256`を参照してください。
