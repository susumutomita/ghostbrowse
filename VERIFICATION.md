# 検証結果 — Ghostbrowse 0.1.0

## 初回作成環境での結果

**52件成功、4件スキップ。失敗0件。**

この結果は同梱した `evidence/pytest.txt` と `evidence/tests.xml` に記録された初回検証の結果です。
実行環境はLinux、Python 3.13.5、Playwright 1.57.0、システムChromium 144.0.7559.96です。
macOSやGhostty本体はこの検証環境では起動していません。

実行コマンド:

```sh
GHOSTBROWSE_TEST_CHROMIUM=/usr/bin/chromium \
GHOSTBROWSE_TEST_NO_SANDBOX=1 \
GHOSTBROWSE_TEST_OFFLINE=1 \
GHOSTBROWSE_EVIDENCE_DIR=./evidence \
python -m pytest -q -ra --junitxml=evidence/tests.xml
```

この環境のChromium管理ポリシーはlocalhostを含むHTTP通信を拒否したため、ブラウザ操作テストでは
作成したHTMLを `page.set_content()` で直接読み込みました。ポリシーは変更していません。
描画・JavaScript・キーボード・マウス処理は実Chromiumを使っていますが、HTTP取得を確認したとは扱いません。

`GHOSTBROWSE_TEST_NO_SANDBOX` は隔離されたrootのテスト環境で作成済みテストHTMLを扱うための設定です。
製品CLIはこの変数を読みません。通常起動ではサンドボックスを有効にし、root実行を拒否します。

## 確認範囲

単体テスト40件では、URLの正規化と危険なスキームの拒否、分割UTF-8・エスケープ列、貼り付け、
Ctrlキー、Kittyキーボード入力、マウスイベント、端末応答の分離、座標変換、アドレス編集、
PNGの4096バイト単位の分割転送を確認しました。

実Chromiumの10件では、クリックとJavaScript、フォーム、日本語・複数行入力、キーイベント、
ホイール、リサイズ、選択文字取得、タブ操作、ダイアログの明示的取消、ドラッグ解除、
パスワード欄をコピーしないこと、2倍画像密度とCSS座標の対応、ASCII記号入力を確認しました。

PTYのE2Eテスト2件では、実子プロセスとChromiumを起動し、端末入力からクリック、日本語貼り付け、
文字入力、リサイズを行いました。端末能力の応答はテストドライバーが返しています。
セル座標・ピクセル座標の両方で、PNGの900×540から720×396への変更を確認しました。
Ctrl+QとSIGTERMによる終了はいずれもコード0で、元のtermios設定への復帰と端末モード解除を確認しました。
Python構文確認と `sh -n install.sh` も実行しています。

## 初回環境で未実行の4件

| テスト | 未確認だった範囲 |
|---|---|
| navigation_history_popups_and_close | HTTPページの履歴・リンク起点のポップアップ |
| profile_persistence | HTTPオリジンのCookie・localStorageの再起動後の保持 |
| controller_terminal_events_use_real_browser | URLバーからHTTPページへ移動する一連の操作 |
| actual_http_fetch | fetchによるHTTP API呼び出し |

通常の環境とGitHub Actionsでは、この4件もスキップせず実行します。

## GitHub登録時の依存修正とCI

初回の固定指定 `playwright==1.62.0` は、GitHub Actionsのパッケージインデックスで取得できず、
依存インストール段階で失敗しました。`requirements.txt` をパッケージ定義と同じ
`playwright>=1.57.0,<2.0` に揃え、設定済みインデックスで取得できる互換リリースを選ぶよう修正しました。
これは全対応バージョンの検証完了を意味しません。

`.github/workflows/test.yml` はLinux/macOSとPython 3.10/3.13の組み合わせを実行します。
最新の実行結果はリポジトリのActionsを参照してください。上記52件成功という過去の結果を
最新CIの成功結果として読み替えないでください。

## 実機未確認事項と証拠

Mac上のGhosttyの実描画、Retinaの見え方、IME、pbcopy、任意の外部サイトのログイン・認証・決済フローは
初回環境では未確認です。macOSのCIが成功しても、Ghostty本体やIME操作を実機確認したことにはなりません。

`evidence/pytest.txt` と `evidence/tests.xml` は初回のテスト結果です。
`evidence/pty-key-result.json` と `evidence/pty-signal-result.json` はPTY検証値です。
`evidence/pty-key-frame.png` と `evidence/pty-signal-frame.png` はアプリ出力から復元したPNGです。
これらは**Ghosttyのスクリーンショットではありません**。実Chromiumから送出されたフレームの検証資料です。
