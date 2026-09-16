Windows版のSDK探索先と、macOS版の既定保存先を修正しました。

- Windowsは `C:\Program Files\Teledyne\Spinnaker\bin64\vs2015\SpinnakerC_v140.dll` を最初に探します。従来のDLL名の誤りを修正し、環境変数を設定しなくてもこのインストール先を使えます。`SPINNAKER_C_LIBRARY` を指定した場合は、その指定を優先します。
- macOSは `DualHolo.app` と同じフォルダの `captures` に保存します。`~/Pictures/DualHolo/captures` には自動保存しません。アプリの隣に書き込めない場合はエラーを表示するので、Finderで書き込み可能なフォルダへ移動して起動し直すか、`--output` で保存先を指定してください。
- 明示的な `--config` の相対保存先は、引き続き設定ファイルの場所が基準です。

Windowsの起動エラーダイアログ、詳細ログ、`Run.cmd` で失敗時にコンソールを残す動作は継続します。

Windows x64、Linux x64、macOS Apple Silicon / Intel向けの撮影アプリです。

- 各OSのアーカイブを展開して起動します。OpenCVは同梱済みです。
- 実カメラには、対応するSpinnaker 4.x C APIランタイムとドライバを別途インストールしてください。
- SDKなしでも `--simulate` で模擬撮影できます。
- 起動時Rec OFF。R / Recボタンで録画開始と停止、Nで表示コントラスト、Q / Escで終了します。
- CIではGUIが指定時間動き続けることを確認します。Linuxはウィンドウの表示、Rによる録画開始、「×」で終了した際の保存完了も検証します。各OSの実カメラ動作は未確認です。
- macOSは `.app` 単体をFinder経由で起動し、内蔵設定、アプリの隣の保存先、GUIの継続描画を検証します。保存先に書き込めない場合のエラーと、`--output` による上書きも確認します。
- Windowsの既定DLL探索は、実SDKと同じフォルダ構成に置いた模擬DLLで検証します。
- Windowsでは模擬入力による両ランチャーのGUI起動と、SDK不足・設定不正の際にエラーダイアログとログが残ることも検証します。
- macOSはアドホック署名のみで未公証、Windowsは未署名です。

詳しい準備と起動方法は、同梱の `distribution.md` を参照してください。
