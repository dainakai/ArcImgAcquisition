Windows x64、Linux x64、macOS Apple Silicon / Intel向けの撮影アプリです。

- 各OSのアーカイブを展開して起動します。OpenCVは同梱済みです。
- 実カメラには、対応するSpinnaker 4.x C APIランタイムとドライバを別途インストールしてください。
- SDKなしでも `--simulate` で模擬撮影できます。
- 起動時Rec OFF。R / Recボタンで録画開始と停止、Nで表示コントラスト、Q / Escで終了します。
- CIでは録画テストと配布アーカイブの模擬実行を検証しています。各OSの実カメラ動作は未確認です。
- macOSはアドホック署名のみで未公証、Windowsは未署名です。

詳しい準備と起動方法は、同梱の `distribution.md` を参照してください。
