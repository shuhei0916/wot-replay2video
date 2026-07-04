# todo.md — タスク管理

このファイルでプロジェクトのタスクを管理する（将来タスクも含む）。
完了したら「完了済み」セクションに移す。

---

## 次にやる

- [ ] **YouTube Studio で無音の3本を削除**（アップロードスコープの OAuth では API 削除
      不可のため手動で）: BHPe3ycmFAQ / z-jQb2ZVg0Q / 0yTtrVBpNec
      （upload_backlog.py の初回実行時、無音ガード追加前に旧 Shorts が混入した）
- [ ] 毎日 `python -u upload_backlog.py` を実行してバックログを消化
      （クォータの都合で1日5本まで。タスクスケジューラ化の候補）

- [ ] 無音期間（6/21〜7/2）の再録画バッチを完走させる（2026-07-03 実行中。
      対象 ~113本、`output/batch_20260703.log` に進捗。resumable なので
      中断しても `python -u run_batch.py` で再開可能）
- [ ] 再録画完了後、生成された Shorts の品質を確認して厳選アップロード
      （`config.yaml` の `youtube.enabled` を true に戻す）
- [ ] `config.yaml` の `youtube.privacy` を `"public"` に変更（公開運用開始時）

## 改善

- [ ] **タスクスケジューラでの夜間バッチ自動実行**: Windows タスクスケジューラ
      から `python -u run_batch.py` を定期起動（サブPC運用）。Claude Code の
      常駐セッションは使わない（使用量・信頼性の観点から `claude -p` 方式に限定）
- [ ] OBS のキャプチャを monitor_capture から **window_capture（WoT ウィンドウ指定）**
      に変更する検討: 前面化に依存しない恒久対策。録画中に PC を操作しても
      映像が汚れない（2026-07-03 のフォアグラウンドロック事故の再発防止）
- [ ] 音声ピーク検出の閾値チューニング: フルバトル録画で 19 ピーク/戦闘は妥当だが、
      Shorts のようなダイナミックレンジが狭い音声では検出漏れする
      （ノイズフロアを中央値でなく低パーセンタイルにする案）

## mod ベースのハイライト検出（実証済み・統合待ち）

2026-07-04 に mod によるゲーム内射撃イベント取得を実証し、パイプライン統合済み。
E2E 検証: fishing_bay で mod イベント7件 → 誤検出ゼロの7クリップ Shorts を生成。

- [x] pipeline 統合（サイドカー `.meta.json`/`.events.json`、`src/detect_mod_events.py`、
      検出優先順位 mod > 音声+輝度融合 > 輝度のみ）
- [x] preflight に mod 未配置の警告を追加（クライアント更新後の検知）
- [ ] mod イベントの拡張: 命中・貫通・撃破・被弾（PlayerAvatar の
      フィードバック系フック）→ ハイライトのスコアリング精度向上
- [ ] バトル開始フック（onArenaPeriodChange）が発火しなかった件の調査（任意。
      epoch 方式では不要だが、リザルト画面除外などに使える）

## 調査・将来

- [ ] **音声ベースのハイライト検出**: 録画の音声トラックから砲撃音（大音量の
      過渡スパイク）を検出する。無音問題解決により利用可能になった。
      輝度フラッシュとの AND/OR 融合で誤検出を削減できる見込み。
- [ ] **イベント検知 mod の再挑戦**: リプレイ再生中にゲーム内イベント
      （射撃・命中・撃破）をタイムスタンプ付きで JSON 出力する mod。
      成功すれば CV 不要で正確なハイライト区間が得られる。
      既存の着手物: `feature/mod-event-extraction` ブランチ、`mods/build_wotmod.py`、
      `test_mod.py`（動作未達成）
- [ ] **リプレイバイナリ復号の再調査**: 既知の旧 AES キーは v2.3.0.0 で不可。
      コミュニティのパーサ実装・別キー/別暗号（Blowfish 等）の可能性を調査
- [ ] `detect_ui_events.py`（UI 領域差分検出）のパイプライン統合 or 削除の判断

## 完了済み

- [x] OBS 自動起動 + 録画前プリフライトチェック（`src/preflight.py`。音声設定検査 +
      テスト録音検証。無音録画ガード・前面化ガードをバッチに統合）
- [x] `run_batch.py` の glob 化（日付 + クライアントバージョンフィルタ）
- [x] 音声スパイク検出（`src/detect_audio_events.py`。輝度フラッシュと融合。
      実録画で 19 音声ピーク + 12 フラッシュ → 28 イベントを確認）
- [x] `claude -p` LLM タイトル生成（`src/llm_title.py`。実呼び出しで動作確認済み、
      テンプレートフォールバック付き）
- [x] WoT ウィンドウ前面化（フォアグラウンドロック対策。2026-07-03 未明の
      バッチが74本全てターミナル画面を録画した事故の修正）
- [x] YouTube アップロードパイプライン（OAuth2 / 再開可能アップロード / 重複防止）
- [x] OBS 録画無音問題の解決（原因: Windows ミキサーで WoT が個別ミュート。
      OBS は正常。診断: `ffprobe -select_streams a` で bit_rate ~2300 なら無音）
- [x] `feature/youtube-upload` を main にマージ・push
- [x] リファクタリング: `src/config.py` 新設（config 読み込み・ffmpeg 探索の集約）、
      pipeline の責務分割、タイトル生成の二重実行解消
- [x] Shorts 合計時間上限の実装（`select_clips()`、150秒 = 2分半）
- [x] テストスイート修復: 81 passed / 9 skipped / 警告なし
      （腐敗テスト修正・リプレイフィクスチャのコミット・edit_video テスト追加）

---

## テスト方針（メモ）

外部プロセス（WoT・OBS・YouTube API 通信）のラッパーはテスト対象外。
モックにすると「モックのテスト」になり費用対効果が低い。
テストを書くのは外部依存のない純粋ロジックのみ
（parse_replay のパース、edit_video のクリップ選択、upload_youtube のメタデータ構築等）。
