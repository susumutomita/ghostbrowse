# 検証結果 — Ghostbrowse 0.1.0

## 実行結果

**52件成功、4件スキップ。失敗0件。**

実行環境はLinux、Python 3.13.5、Playwright 1.57.0、システムChromium
144.0.7559.96です。macOSやGhostty本体はこの環境では起動していません。

インストーラーは2026年9月15日に公開情報を確認したPlaywright **1.62.0** を固定して取得します。
この環境では新しい依存パッケージを取得できなかったため、1.62.0とその同梱Chromiumの組み合わせは未実行です。
ライブラリの対応下限は1.57.0に設定し、そのAPIで実装しています。

検証時の実行コマンド:

```sh
GHOSTBROWSE_TEST_CHROMIUM=/usr/bin/chromium \
GHOSTBROWSE_TEST_NO_SANDBOX=1 \
GHOSTBROWSE_TEST_OFFLINE=1 \
GHOSTBROWSE_EVIDENCE_DIR=./evidence \
python -m pytest -q -ra --junitxml=evidence/tests.xml
```

検証環境のChromiumにはすべてのURL通信をブロックする管理ポリシーがあり、
localhostへのHTTP接続も拒否されました。ポリシーは変更していません。
ブラウザ操作のテストには、作成したHTMLを `page.set_content()` で直接読み込んでいます。
Chromiumの描画・JavaScript・キーボード・マウス処理は本物で、ブラウザ自体のモックは使っていません。
一方、HTTP取得を検証できたとは扱っていません。

`GHOSTBROWSE_TEST_NO_SANDBOX` はrootで動く隔離された検証環境内で、作成したテストHTMLだけを扱うための
テスト側の設定です。製品CLIはこの環境変数を読みません。通常起動はChromiumサンドボックスを有効にし、
root実行を拒否します。一般サイト閲覧のためにサンドボックスを無効にする手順ではありません。

## 確認した内容

単体テスト40件で、URL入力の正規化・危険なスキームの拒否、分割されたUTF-8、分割されたエスケープ列、
ブラケット付き貼り付け、Ctrlキー、Kittyキーボード入力、マウスイベント、端末応答の分離、
画面寸法と座標変換、アドレス編集、画像の4096バイト単位での分割転送を確認しました。

実Chromiumを使うテスト10件で、クリックによるJavaScriptの実行、フォーム入力、日本語と複数行文字列、
キーイベント、ホイール、画面サイズ変更、選択文字列取得、タブ作成・切替・終了、開始ページ、
ダイアログの明示的な取消、ドラッグ解除、パスワード欄をコピーしないこと、
Retina相当2倍密度の画像とCSS座標の対応、ASCII記号入力を確認しました。

PTYのE2Eテスト2件では、アプリを実際の子プロセスとして起動しました。
テストドライバーが画像プロトコル能力・セル寸法の問い合わせへ応答し、
端末入力を通じてクリック、日本語貼り付け、文字入力、リサイズを行っています。
セル座標とピクセル座標の両方をテストしました。
出力されたKittyプロトコルから実PNGを復元し、900×540から720×396へのサイズ変更を確認しています。
Ctrl+Q終了とSIGTERM終了の両方で終了コード0、端末の元のtermios設定への復帰、
代替画面・マウス・キーボードモードの解除を確認しました。

追加で全Pythonモジュールの構文確認と `sh -n install.sh` を実行しています。

## 未実行の4件

| テスト | 未確認の範囲 |
|---|---|
| navigation_history_popups_and_close | HTTPページ間の戻る・進む、リンク起点のポップアップ |
| profile_persistence | HTTPオリジンのCookie・localStorageのプロセス再起動後の保持 |
| controller_terminal_events_use_real_browser | URLバーから実HTTPページへ移動する操作を含む一連の操作 |
| actual_http_fetch | ページのfetchによるHTTP API呼び出し |

この4件のテストコードは同梱し、通常の環境ではスキップしません。

## その他の未確認範囲

Mac上のGhosttyでの画像プロトコル実描画、Retina表示の見え方、macOSのIME、pbcopyによるコピー、
インストーラーによるネットワーク経由の依存取得、Playwright 1.62.0とその同梱Chromium、
任意の外部サイト・ログイン・認証・決済フローは未確認です。
GitHub Actions定義は含めていますが、リモートで実行した事実はありません。

## 証拠ファイル

`evidence/pytest.txt` が実テスト結果、`evidence/tests.xml` がJUnit形式の結果です。
`evidence/pty-key-result.json` と `evidence/pty-signal-result.json` はE2Eの検証値です。
`evidence/pty-key-frame.png` と `evidence/pty-signal-frame.png` はアプリの出力から復元した画面画像です。
これらは**Ghosttyのスクリーンショットではありません**。実Chromiumの画像フレームを検証した証拠です。

「実装済み」「この環境での検証済み」「Mac実機での検証済み」を分けて扱っています。
