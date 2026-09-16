macOS版がApp Translocationの一時フォルダを保存先にしてしまい、読み取り専用エラーで起動できない問題を修正しました。
macOSから元のアプリ配置先を取得し、ダウンロードした `DualHolo.app` と同じフォルダの `captures` に保存します。
保存先を合わせるためにアプリを移動する必要はありません。

- Windowsは `C:\Program Files\Teledyne\Spinnaker\bin64\vs2015\SpinnakerC_v140.dll` を最初に探します。従来のDLL名の誤りを修正し、環境変数を設定しなくてもこのインストール先を使えます。`SPINNAKER_C_LIBRARY` を指定した場合は、その指定を優先します。
- macOSはApp Translocationで起動しても、元の `DualHolo.app` の隣に保存します。元の配置先にも書き込み権限がない場合だけ、権限の確認または `--output` による指定が必要です。
- 明示的な `--config` の相対保存先は、引き続き設定ファイルの場所が基準です。

Windowsの起動エラーダイアログ、詳細ログ、`Run.cmd` で失敗時にコンソールを残す動作は継続します。

Windows x64、Linux x64、macOS Apple Silicon / Intel向けの撮影アプリです。

- 各OSのアーカイブを展開して起動します。OpenCVは同梱済みです。
- 実カメラには、対応するSpinnaker 4.x C APIランタイムとドライバを別途インストールしてください。
- SDKなしでも `--simulate` で模擬撮影できます。
- 起動時Rec OFF。R / Recボタンで録画開始と停止、Nで表示コントラスト、Q / Escで終了します。
- CIではGUIが指定時間動き続けることを確認します。Linuxはウィンドウの表示、Rによる録画開始、「×」で終了した際の保存完了も検証します。各OSの実カメラ動作は未確認です。
- macOSは通常起動に加え、本物のApp Translocation起動も再現し、元の `.app` の隣への模擬録画画像の保存、GUIの継続描画、起動時Rec OFFを検証します。保存先に書き込めない場合のエラーと、`--output` による上書きも確認します。
- Windowsの既定DLL探索は、実SDKと同じフォルダ構成に置いた模擬DLLで検証します。
- Windowsでは模擬入力による両ランチャーのGUI起動と、SDK不足・設定不正の際にエラーダイアログとログが残ることも検証します。
- macOSはアドホック署名のみで未公証、Windowsは未署名です。

詳しい準備と起動方法は、同梱の `distribution.md` を参照してください。
