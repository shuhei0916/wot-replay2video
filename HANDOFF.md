# HANDOFF.md

最終更新: 2026-07-02

---

## 現在のブランチ

`feature/youtube-upload`（mainへの未マージ）

---

## 完了済み

### YouTube アップロードパイプライン
- `src/upload_youtube.py` — OAuth2認証、再開可能アップロード、重複防止ログ
- `src/pipeline.py` — Shorts生成後に `_try_upload()` で自動アップロード
- `tests/test_upload_youtube.py` — 純粋ロジック24テスト（全パス）
- `config/client_secrets.json` — Google Cloud OAuth認証情報（.gitignore済み）
- `config/token.json` — 初回OAuth認証済み（.gitignore済み）
- `config.yaml` に `youtube:` セクション追加（privacy/category_id/default_tags）
- アップロードテスト成功: https://youtu.be/9p-ZfVG0Tqg（非公開、T34 1）

### バグ修正（このセッションで発見・修正）
- `src/launcher.py` / `src/recorder.py` / `src/batch.py`: `config.yaml` を
  `read_text()` で読む際に `encoding="utf-8"` が抜けていた。
  `"戦車"` などの日本語を含む設定でサイレントに失敗しパスワードが空になっていた。
- `src/pipeline.py`: `wait_for_replay_start(timeout=120)` を 300 に延長
  （WoT起動に120秒以上かかることがある）

---

## 解決済み: OBS 音声が無音問題（2026-07-02）

### 根本原因
**Windows のサウンドミキサーで WoT アプリだけが個別ミュートされていた**
（夜間バッチの静音化のためユーザーが設定）。アプリ個別ミュートは
WASAPI ループバック（OBS のデスクトップ音声）にも乗らないため録画が無音になる。

### 切り分けの経緯
- 6/19 の録画は音声あり（193kbps）、6/29 以降は無音（~2275bps）
- OBS 設定は WebSocket 検査で正常（ミュートなし・0dB・トラック1割り当て済み）
- OBS 単体テスト（テスト音を鳴らして録画）→ 正常録音 → OBS は無罪と確定
- ミュート解除後の検証録画: 163kbps / mean -27.0dB / max -4.4dB で正常

### 診断コマンド
```
ffprobe -v error -show_streams -select_streams a -of default <ファイル.mp4>
```
`bit_rate` が 128000 程度なら正常、2000 前後なら無音。
実音量は `ffmpeg -i <file> -af volumedetect -f null -` で確認。

### 静音バッチ実行したい場合の注意
Windows ミキサーやゲーム内でミュートすると録画も無音になる。
静音化するなら VB-Cable 等の仮想オーディオデバイスを既定の再生デバイスに
する方法を使う（スピーカーは鳴らず、OBS はそこから録れる）。

### FFmpeg への乗り換えは見送り
- 無音の原因は OBS ではなかった
- この環境は DXGI キャプチャが黒画面になる実績があり（OBS method=1）、
  ffmpeg の ddagrab も同じ罠にはまる可能性が高い
- ffmpeg は WASAPI ループバック非対応（ステレオミキサー等の追加設定が必要）
- 「人間による OBS 起動・設定チェック」のボトルネックは、パイプラインから
  obs64.exe を自動起動し WebSocket でプリフライトチェック（ミュート/トラック/
  デバイス確認 + 数秒テスト録画の volumedetect）する形で解消可能（未実装）

---

## リファクタリング済み（2026-07-02, refactor/pipeline-cleanup）

- `src/config.py` 新設: config.yaml 読み込みと ffmpeg 探索を集約
  （従来は4ファイルに読み込みコードが重複し、encoding バグの温床だった）
- `pipeline.py` を責務分割: `record_replay()`（録画のみ）/
  `make_highlight_shorts()` / `build_title()` / `upload_shorts()` /
  `process_replay()`（オーケストレーション）。タイトル生成の二重実行を解消。
- `edit_video.py`: Shorts 60秒上限を実装（`select_clips()`。従来 `SHORTS_MAX_SEC`
  が未使用でクリップ9本以上だと60秒超過するバグがあった）
- テスト: 81 passed / 9 skipped / 警告なし
  - 腐敗していた detect_highlights テストを修復（skip_initial_sec / イベント名）
  - テスト用リプレイを `tests/fixtures/` にコミット（parse_replay の25 ERRORを解消）
  - `tests/test_edit_video.py` 新規（クリップ選択ロジック）

---

## 次のタスク（優先順）

1. ~~OBS音声問題を解決~~（解決済み・上記参照）
2. `config.yaml` の `privacy` を `"public"` に変更（現在 `"private"`）
3. ~~`feature/youtube-upload` を `main` にマージ~~（完了・push済み）
4. 新しいリプレイのバッチ実行（2026-07-02 の新着リプレイあり。無音問題があった
   6/29〜7/2 処理分は音声なしで生成されているため再録画・再生成を検討）
5. (任意) OBS 自動起動 + 録画前プリフライトチェックの実装
6. (任意) `run_batch.py` のハードコードされたリプレイリストを Drive フォルダの
   glob + processed.json フィルタに置き換え

---

## 主要ファイル

| ファイル | 役割 |
|---------|------|
| `src/pipeline.py` | メインパイプライン（録画→ハイライト→Shorts→アップロード） |
| `src/upload_youtube.py` | YouTube APIラッパー |
| `src/recorder.py` | OBS WebSocket録画制御 |
| `src/launcher.py` | WoT起動・リプレイ検出 |
| `src/batch.py` | バッチ処理 |
| `run_batch.py` | 実行スクリプト（リプレイリスト直書き） |
| `config/client_secrets.json` | Google OAuth認証情報（要保管） |
| `config/token.json` | アクセストークン（要保管） |
| `output/uploaded.json` | アップロード済み動画ログ |
