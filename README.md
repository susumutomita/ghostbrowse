# Ghostbrowse

**Ghosttyの同じペインの中で、Webページを表示・操作するブラウザコマンド。**

Ghostty本体をフォークするのではなく、ローカルのChromiumで描画した画面を
Kitty Graphics Protocolで端末へ送り、端末のマウス・キーボード入力をChromiumへ渡します。
テキスト専用ブラウザでも、外部のChromeを開くランチャーでもありません。
Ghosttyの設定ファイルには変更を加えません。

## インストールと起動

macOS / Linux、Python 3.10以降、Ghosttyが対象です。tmux・screen・zellijを介さず、
Ghosttyのペインで直接実行してください。インストール時にはインターネット接続が必要です。

リポジトリをcloneし、`ghostbrowse` ディレクトリ内で実行します。
ZIPを取得した場合は、展開したディレクトリで `sh install.sh` から実行してください。

```sh
git clone https://github.com/susumutomita/ghostbrowse.git
cd ghostbrowse
sh install.sh
~/.local/bin/ghostbrowse http://localhost:3000
```

Pythonがない場合、macOSでは先に `brew install python` で用意してください。
インストール先は `~/.local/share/ghostbrowse/venv`、コマンドは `~/.local/bin/ghostbrowse` です。
`sudo` は不要で、rootでの実行は拒否します。Chromiumもインストーラーが取得します。

コマンド名だけで呼ぶ場合は、利用中のシェルで次を実行します。

```sh
export PATH="$HOME/.local/bin:$PATH"
ghostbrowse localhost:3000
```

URLなしで起動すると、開始ページを表示します。

```sh
~/.local/bin/ghostbrowse
```

Ghosttyの分割ペインの片側で開けば、もう片側にシェルや開発サーバーを残せます。
`Ctrl+Q` でブラウザを終了すると、そのペインの元のシェルへ戻ります。

## 操作

ここでの `Ctrl` はControlキーです。Commandキーではありません。

| 操作 | キー・入力 |
|---|---|
| URLを開く | `Ctrl+L`、URL入力、`Enter`。URL行のクリックでも入力できます |
| URL入力を中止 | `Esc` |
| リンク・ボタン | ページ内をクリック |
| スクロール | マウスホイール・トラックパッド |
| フォーム入力 | 入力欄をクリックして入力。日本語・複数行貼り付けに対応 |
| 貼り付け | Ghosttyの貼り付け操作。ブラケット付き貼り付けを文字列として処理します |
| 戻る・進む | `F7` / `F8`。端末が送信する場合は `Alt+←` / `Alt+→` も対応 |
| 再読み込み | `Ctrl+R` |
| 新しいブラウザタブ | `Ctrl+T` |
| 次・前のブラウザタブ | `Ctrl+N` / `Ctrl+P` |
| 現在のブラウザタブを閉じる | `Ctrl+W`。最後のタブを閉じると開始ページへ戻ります |
| ページの選択テキストをコピー | ページ内でドラッグして `Ctrl+B`。パスワード欄はコピーしません |
| URLをコピー | `Ctrl+Y` |
| JavaScriptダイアログ | `F9` で承認、`F10` で取消。prompt文字列の編集には未対応 |
| ヘルプ表示 | `F1` |
| 終了・シェルへ戻る | `Ctrl+Q` または `Ctrl+C` |

Macのファンクションキー設定によっては `fn` と一緒に押してください。
ブラウザのタブとGhosttyのタブは別です。
画面を画像として表示するため、Ghostty自身のテキスト選択ではWebページの文字をコピーできません。
上記の `Ctrl+B` を使用してください。

## プロファイル・表示設定

既定では専用プロファイルのCookieとlocalStorageを保持します。
普段使っているChrome/Safariのプロファイルにはアクセスしません。
保存先は `~/.local/share/ghostbrowse/profiles/default` で、ディレクトリ権限は700です。
`XDG_DATA_HOME` を設定している場合は、その配下に保存します。

別のペインで同時起動する場合は、別名のプロファイルまたは一時プロファイルを使います。

```sh
~/.local/bin/ghostbrowse --profile work https://example.com
~/.local/bin/ghostbrowse --incognito http://localhost:5173
```

同じプロファイルを複数プロセスで同時使用することはできません。
`--incognito` は終了時に一時プロファイルを破棄する設定であり、通信の匿名化や安全な消去を保証するものではありません。

```sh
~/.local/bin/ghostbrowse --zoom 1.25 --theme light http://localhost:3000
~/.local/bin/ghostbrowse --fps 8 --idle-fps 8 http://localhost:3000
~/.local/bin/ghostbrowse --retina 1 http://localhost:3000
~/.local/bin/ghostbrowse --doctor
```

`--zoom` は0.5〜3。macOSの画像密度は既定で2倍です。
転送量を下げる場合は `--retina 1` を使用します。
既定の更新目標は操作中8fps、2秒操作がないと1fpsです。実際のfpsはページや端末に依存します。
画面が変化していない場合、同じ画像を再送しません。

文字が縦横に歪む場合は `--doctor` の情報とGhosttyのバージョンを確認してください。
セルの実ピクセル寸法は端末へ問い合わせます。応答がない場合は9:18の比率を仮定します。
Retinaのピクセル数をそのままCSSサイズにせず、文字が小さくなりすぎないよう変換しています。

## 実装の範囲

JavaScript実行、画像・CSSの描画、マウスクリック、ホイール、入力、タブ、履歴、
リサイズ、専用プロファイル保存を実装しています。
描画にはPNGの直接転送と2枚の交互バッファを使い、古い画像のデータも解放します。

これはネイティブWebViewをGhosttyのUIへ埋め込むパッチではありません。
動画・音声、DRM、ブラウザ拡張、Passkey、ネイティブ認証UI、DevToolsの完全な代替にはなりません。
ダウンロードは拒否し、ファイルアップロードの選択は未実装です。
ネイティブの選択メニューや右クリックメニューは画像に含まれない場合があります。
日本語の確定済み文字列は渡せますが、IMEの未確定文字・候補ウィンドウをWeb入力欄へ
ネイティブに結びつける実装ではありません。Mac上のIME操作は未検証です。

ログインを保持する仕組みはありますが、特定サイトのログイン成功、Google等の自動化対策、
決済や認証フローの互換性は保証していません。通常のブラウザを全面的に置き換える版ではありません。

## セキュリティ上の扱い

ブラウザはローカルで起動します。ブラウザ制御用のTCPポートや外部中継サービスは開きません。
訪問したWebサイトへの通信は通常どおり発生します。
ChromiumのサンドボックスとTLS証明書検証は有効です。
Playwrightの既定のポップアップブロック解除フラグは除外しています。
権限を全サイトに一括付与したり、JavaScript確認ダイアログを自動承認したりしません。

Webページのタイトル・URL・エラー文に含まれる端末制御文字は、端末へ表示する前に除去します。
貼り付けはショートカットキー列として評価しません。
クリップボード操作はユーザーがコピーキーを押した時だけ行います。
入力内容、Cookie、画像フレームをアプリ自身が自動でログファイルへ保存することはありません。
Chromium自身のプロファイル保存と、明示的に行うコピーは別です。

## テスト

依存関係を入れた開発用環境では、次のコマンドで検証できます。
通常の環境ではHTTP通信を含む全テストを実行します。

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install '.[test]'
python -m playwright install chromium
python -m pytest -q
```

Linuxでブラウザのシステム依存がない場合は、Playwrightの公式手順に従って依存を用意してください。
macOS/LinuxのGitHub Actions定義を `.github/workflows/test.yml` に同梱しています。
最新のCI実行状況は、このリポジトリのActionsタブで確認してください。

初回作成環境での検証結果・未確認範囲は `VERIFICATION.md` を参照してください。
疑似端末テストは本物のChromiumとPTYを使いますが、Ghosttyそのものを起動するテストではありません。
テスト用HTML・テスト用のChromium起動設定を、製品のブラウザ処理と混同しないでください。

## ソースの構成

`ghostbrowse/browser.py` がChromium操作、`protocol.py` が入力・画像プロトコル、
`terminal.py` が端末状態・描画、`app.py` が操作と画面更新、`cli.py` が起動設定です。
`tests/` に単体・ブラウザ・PTYテスト、`scripts/pty_probe_child.py` にPTY検証用の子プロセスがあります。
インストールされるPythonパッケージにテスト用スクリプトは含めません。

## 参照した公式資料

- Ghosttyの画像・キーボードプロトコル対応: https://ghostty.org/docs/features
- Kitty Graphics ProtocolのPNG転送・画像配置・削除: https://sw.kovidgoyal.net/kitty/graphics-protocol/
- Playwrightのブラウザ起動とプロファイル: https://playwright.dev/python/docs/api/class-browsertype
- キーボード: https://playwright.dev/python/docs/api/class-keyboard
- マウス: https://playwright.dev/python/docs/api/class-mouse
- インストーラーの固定バージョン情報: https://pypi.org/project/playwright/

このプロジェクトはGhostty公式プロジェクトではありません。Apache-2.0 License。
