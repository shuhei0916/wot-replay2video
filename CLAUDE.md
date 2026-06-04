# wot-replay2video

World of Tanks のリプレイを自動で録画・ハイライト編集して YouTube Shorts 動画を生成するプロジェクト。

## 目標フロー

```
.wotreplay ファイル
    ↓ (1) WoTクライアント自動起動・リプレイ再生
スクリーン録画（フル戦闘映像）
    ↓ (2) ハイライト検出（コンピュータビジョン）
ハイライト区間タイムスタンプ
    ↓ (3) FFmpeg でカット編集・縦動画化
YouTube Shorts 動画（9:16, 最大60秒）
```

## 環境

- **WoT クライアント**: Windows (`X:\Games\World_of_Tanks_ASIA`)
  - リプレイ保存先: `X:\Games\World_of_Tanks_ASIA\replays\`
- **開発環境**: WSL2 (Ubuntu) + Claude Code
- **言語**: Python 3
- **プロジェクトルート**: `C:\Users\Ito\projects\wot-replay2video`
  - WSL パス: `/mnt/c/Users/Ito/projects/wot-replay2video`

## .wotreplay ファイル形式

```
[4B] magic: 12 32 34 11
[4B] ブロック数 (通常 2)
[4B + NB] Block 1 (JSON): バトル開始前メタデータ
[4B + NB] Block 2 (JSON): バトル結果サマリー (リスト形式, 3要素)
[残り]     バイナリ: ゲームパケット録画 (AES-128 暗号化)
```

### Block 1 の主要フィールド
- `playerName`, `playerVehicle`, `mapDisplayName`
- `clientVersionFromExe`, `regionCode`, `serverName`
- `vehicles`: 全参加車両の事前情報 (チーム, 車種, プレイヤー名)

### Block 2 の主要フィールド (`[0]` 要素)
- `common`: 戦闘時間 (`duration`)、勝利チーム (`winnerTeam`)、終了理由 (`finishReason`)、マップID
- `personal`: 自プレイヤーの詳細成績 (キル, ダメージ, 命中数, XP, クレジット等)
- `vehicles`: 全車両の成績 (vehicleID → stats)
- `players`: playerID → 名前・クラン・チーム
- `avatars`: playerID → アバター統計

### バイナリセクション
AES-128-CBC 暗号化。既知の旧キー `de72bef0a09bb439d37c59c3df1fc194` では v2.3.0.0 は復号不可。
復号できれば射撃タイミング・車両座標・ヒット/キルイベントが取得できる。
**現状は JSON メタデータのみ利用可能。**

## ハイライト検出方針（バイナリ非復号の場合）

録画後の動画をコンピュータビジョンで解析：
- 着弾フラッシュ（画面輝度の急上昇）
- キル通知 UI（右上のキルアイコン）
- ダメージ数字テキスト
- HP バーの急減

## ディレクトリ構成（予定）

```
wot-replay2video/
├── CLAUDE.md
├── replays/          # 処理対象の .wotreplay を置く場所
├── output/           # 生成動画の出力先
├── src/
│   ├── parse_replay.py    # .wotreplay → メタデータ JSON 抽出
│   ├── launcher.py        # WoT クライアント起動・リプレイ再生自動化
│   ├── recorder.py        # スクリーン録画
│   ├── detect_highlights.py  # ハイライト区間検出 (OpenCV)
│   └── edit_video.py      # FFmpeg ラッパー・Shorts 生成
└── config.yaml            # WoT パス等の設定
```

## 主要依存ライブラリ（予定）

| 用途 | ライブラリ |
|------|-----------|
| スクリーン録画 | `mss` or `pygetwindow` |
| コンピュータビジョン | `opencv-python` |
| 動画編集 | `ffmpeg` (CLI) |
| WoT 操作自動化 | `pywin32` / `subprocess` (Windows 側) |
| 設定 | `pyyaml` |

## 注意事項

- WoT クライアントは Windows で動作。録画・起動自動化は Windows 側のスクリプトまたは WSL から `powershell.exe` / `cmd.exe` 経由で実行する。
- YouTube Shorts の仕様: 縦型 9:16、60秒以内、最小解像度 1080×1920 推奨。
- リプレイファイルのファイル名形式: `YYYYMMDD_HHMM_<nation>-<vehicleID>_<mapID>.wotreplay`
