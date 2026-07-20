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

- **Windows ネイティブ**（録画・再生・開発すべて同一 PC）。実際のパス類は `config.yaml` が正
- **WoT クライアント**: `C:\Games\World_of_Tanks_ASIA`
- **処理対象リプレイ**: Google Drive 同期フォルダ（`config.yaml` の `replays.dir`）
- **言語**: Python 3

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

## 注意事項

- YouTube Shorts の仕様: 縦型 9:16、60秒以内、最小解像度 1080×1920 推奨。
- リプレイファイルのファイル名形式: `YYYYMMDD_HHMM_<nation>-<vehicleID>_<mapID>.wotreplay`
