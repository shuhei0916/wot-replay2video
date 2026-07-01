# YouTube アップロード自動化 TODO

## セットアップ（手動・一度だけ）

- [ ] Google Cloud Console でプロジェクト作成
- [ ] YouTube Data API v3 を有効化
- [ ] OAuth 2.0 クライアント ID を作成（デスクトップアプリ）
- [ ] `client_secrets.json` を `config/` に配置
- [ ] 初回ブラウザ認証 → `config/token.json` 生成

## 実装

- [ ] `src/upload_youtube.py` — YouTube Data API ラッパー
  - [ ] OAuth 認証フロー（初回 + トークンリフレッシュ）
  - [ ] 動画アップロード（再開可能アップロード / resumable upload）
  - [ ] メタデータ設定（タイトル・説明・タグ・カテゴリ・公開設定）
  - [ ] アップロード済みリスト管理（重複防止）
- [ ] `src/batch.py` にアップロードステップを統合
  - [ ] Shorts 生成後に自動アップロード
  - [ ] アップロード失敗時はスキップ（動画は保持）

## 設定

- [ ] `config.yaml` に YouTube 設定セクションを追加
  - [ ] `privacy`: `public` / `unlisted` / `private`
  - [ ] `category_id`: ゲーム = 20
  - [ ] `default_tags`: `["WorldOfTanks", "WoT", "Shorts"]`

## テスト方針

外部プロセス（WoT・OBS・YouTube API通信）のラッパーはテスト対象外とする。
モックにすると「モックのテスト」になり費用対効果が低いため。

テストを書く対象は「外部依存のない純粋ロジック」のみ：
- `parse_replay.py`: パースロジック（既存テストあり）
- `detect_highlights.py`: クリップ選択アルゴリズム（既存テストあり）
- `upload_youtube.py`: 以下の純粋ロジック部分のみ

t-wada 推奨の TDD サイクル（red → green → refactor）で実装する。

## テスト

- [ ] `tests/test_upload_youtube.py`
  - [ ] タイトル文字列からハッシュタグを抽出するロジック
  - [ ] YouTube API 用メタデータ dict の構築ロジック
  - [ ] アップロード済みチェックロジック（ログファイルの読み書き）
  - [ ] HTTP ステータスコードに基づくリトライ判定ロジック

## 動作確認

- [ ] テスト動画（非公開）で実際にアップロード成功を確認
- [ ] `processed.json` と連携して重複アップロードしないことを確認
- [ ] バッチ全体（録画→Shorts生成→アップロード）のE2E確認
