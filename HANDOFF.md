# 引継ぎ資料 — wot-replay2video

## プロジェクト概要

World of Tanks のリプレイファイル (`.wotreplay`) を自動で録画・ハイライト編集して YouTube Shorts 動画を生成するパイプライン。

```
.wotreplay
  → WoT クライアント自動起動・リプレイ再生
  → OBS Studio で画面録画
  → コンピュータビジョンでハイライト検出
  → FFmpeg で Shorts 用縦動画 (9:16, 60秒以内) に編集
```

---

## 環境

| 項目 | 内容 |
|------|------|
| OS | Windows 11 + WSL2 (Ubuntu) |
| WoT インストール先 | `X:\Games\World_of_Tanks_ASIA\` |
| リプレイ保存先 | `X:\Games\World_of_Tanks_ASIA\replays\` |
| 開発・実行環境 | WSL2 上の Python 3 (linuxbrew) |
| プロジェクトルート (Windows) | `C:\Users\Ito\projects\wot-replay2video\` |
| プロジェクトルート (WSL) | `/mnt/c/Users/Ito/projects/wot-replay2video/` |
| 録画ツール | OBS Studio 32.x |
| Python パッケージ管理 | `pip3 --break-system-packages` |

---

## セットアップ手順（新しい PC での初回設定）

### 1. リポジトリのクローン

```bash
git clone <repo-url> /mnt/c/Users/<username>/projects/wot-replay2video
cd /mnt/c/Users/<username>/projects/wot-replay2video
```

### 2. Python パッケージのインストール

```bash
pip3 install opencv-python numpy obsws-python pyyaml --break-system-packages
```

### 3. OBS Studio のインストール・設定

1. https://obsproject.com/ からダウンロード・インストール
2. OBS を起動
3. **シーン・ソースの設定**（重要）：
   - 「ソース」パネルの「+」→「画面キャプチャ」を追加
   - WoT を表示するモニターを選択
   - **「ソース」パネルの「+」→「映像キャプチャデバイス」は不要**
4. **音声の設定**：
   - 「設定」→「音声」→「デスクトップ音声」にスピーカー/ヘッドホンを設定
   - VB-Audio Virtual Cable は不要（OBS の WASAPI ループバックで取得）
5. **出力の設定**：
   - 「設定」→「出力」→出力モード: `詳細`
   - 録画フォーマット: `mp4`、エンコーダ: `NVIDIA NVENC H.264` または `x264`
   - 「設定」→「映像」→解像度: `1920x1080`、FPS: `30`
6. **WebSocket の設定**：
   - 「ツール」→「obs-websocket 設定」
   - 「WebSocket サーバーを有効にする」✅
   - ポート: `4455`
   - 「認証を有効にする」✅ → パスワードを設定

### 4. config.yaml の作成

`config.yaml.example` をコピーして `config.yaml` を作成し、OBS のパスワードを記入：

```bash
cp config.yaml.example config.yaml
```

```yaml
obs:
  host: localhost
  port: 4455
  password: "OBSで設定したパスワード"  # ツール → obs-websocket設定 → パスワードを表示
```

> `config.yaml` は `.gitignore` で除外済み。パスワードは git にコミットされない。

### 5. 接続テスト

OBS を起動した状態で：

```bash
python3 -c "
import obsws_python as obs
cl = obs.ReqClient(host='localhost', port=4455, password='YOUR_PASSWORD', timeout=10)
print('接続成功:', cl.get_version().obs_version)
cl.disconnect()
"
```

---

## 使い方

### リプレイを処理する

```bash
# replays/ フォルダにリプレイを置いて実行
python3 -m src.pipeline replays/<filename>.wotreplay

# replays/ 内の最新ファイルを自動選択
python3 -m src.pipeline
```

出力動画は `output/` フォルダに保存される。

---

## ディレクトリ構成

```
wot-replay2video/
├── CLAUDE.md                  # Claude Code 用プロジェクト設定
├── HANDOFF.md                 # この引継ぎ資料
├── config.yaml                # OBS パスワード等（gitignore 済）
├── config.yaml.example        # config.yaml のテンプレート（コミット済）
├── requirements.txt           # Python 依存パッケージ一覧
├── replays/                   # 処理対象の .wotreplay を置く
├── output/                    # 生成動画の出力先（gitignore 済）
├── src/
│   ├── pipeline.py            # メインパイプライン（起動〜録画〜出力）
│   ├── launcher.py            # WoT 起動・リプレイ再生・終了検出
│   ├── recorder.py            # OBS WebSocket 録画制御
│   ├── detect_highlights.py   # ハイライト検出（輝度フラッシュ）
│   ├── detect_ui_events.py    # UI イベント検出（キル通知等）
│   ├── edit_video.py          # FFmpeg ラッパー・Shorts 生成
│   └── parse_replay.py        # .wotreplay メタデータ解析
└── tests/                     # テストコード
```

---

## 現在の既知の問題・TODO

### 問題: OBS で画面が録画されない（音声は録れる）

**原因**: OBS のシーン設定で「画面キャプチャ」ソースが正しく追加されていない可能性が高い。

**確認手順**:
1. OBS の「ソース」パネルに「画面キャプチャ」が表示されているか確認
2. なければ「+」→「画面キャプチャ」→ WoT を表示しているモニターを選択
3. OBS のプレビュー画面に映像が映っているか確認してから録画

### TODO（未実装・未完成）

| 優先度 | 項目 | 説明 |
|--------|------|------|
| 高 | OBS 画面録画の修正 | 上記の「画面が録画されない」問題の解消 |
| 高 | ハイライト検出の統合 | `detect_highlights.py` + `detect_ui_events.py` を `pipeline.py` に組み込む |
| 高 | Shorts 動画生成 | `edit_video.py` でハイライト区間を縦動画に変換するエンドツーエンドテスト |
| 中 | マルチ PC 対応 | Google Drive でリプレイファイルをメイン PC とサブ PC で同期 |
| 低 | テスト整備 | パイプライン統合テストの追加 |

---

## パイプラインの動作原理

### 起動・終了検出 (`src/launcher.py`)

WoT のログファイル (`python.log`) を監視してリプレイの開始・終了を検出する：
- 開始: `Avatar.onBecomePlayer` がログに出現
- 終了: `Arena.onDeleteVehicle` または `Battle results` がログに出現

### OBS 録画制御 (`src/recorder.py`)

`obsws-python` ライブラリ経由で OBS WebSocket API を呼び出す：
- `obs.ReqClient.start_record()` で録画開始
- `obs.ReqClient.stop_record()` で録画停止（戻り値に保存パスが含まれる）
- OBS が返す Windows パスを `wslpath -u` で WSL パスに変換

---

## 依存関係

```
opencv-python    # コンピュータビジョン（ハイライト検出）
numpy            # 数値計算
obsws-python     # OBS WebSocket クライアント
pyyaml           # config.yaml 読み込み
```

インストール:
```bash
pip3 install -r requirements.txt --break-system-packages
```
