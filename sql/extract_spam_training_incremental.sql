-- ============================================================================
-- Layer 1：被動增量抽取（confirmed-spam 正樣本 + hard-negative ham）
-- 用途：把「已處置/已確認」的留言內容，趁內容還在 live DB，定期落袋成訓練樣本。
-- 對象：留言（comment）。文章/短動態的抽取沿用 extract_training_data.sql（user_restriction
--       + is_spam + archived 作者），本檔專補 Community Watch 覆蓋的留言維度。
--
-- ⚠️ 在 READ-REPLICA 上跑。輸出後做去識別化（見檔尾）再 append 進 S3 訓練桶。
-- ⚠️ schema 對照 matters-server db/migrations（community_watch_action：
--    20260510001000；comment.spam_score/is_spam：comment.d.ts）。
--
-- 參數：
--   :since   增量起點（上次成功匯出的 high-watermark，建議用 updated_at）
--   :spam_threshold  模型分數視為 spam 的門檻（與 systemService.getSpamThreshold 對齊，預設 0.80）
-- ----------------------------------------------------------------------------
\set since '2026-06-01'
\set spam_threshold 0.80

-- ----------------------------------------------------------------------------
-- A. 正樣本（label = spam, 1）
--    來源優先序（強→弱）：
--      s1 守望隊移除且未被推翻（reviewState≠reversed 且 actionState=active）
--      s2 作者被限制（user_restriction）
--      s3 管理員標記 is_spam = true
--      s4 模型分數 spam_score ≥ 門檻
--    一則留言可命中多條，用 label_source 陣列保留來源以利抽檢/加權。
-- ----------------------------------------------------------------------------
WITH cw_removed AS (
  -- 守望隊移除：originalContent 是被移除當下的文字快照（可能日後被 clear → 趁在就抓）
  SELECT
    cwa.comment_id,
    cwa.comment_author_id            AS author_id,
    COALESCE(cwa.original_content, c.content) AS content,
    cwa.reason                       AS cw_reason,        -- porn_ad | spam_ad
    cwa.review_state,
    cwa.action_state,
    cwa.updated_at
  FROM community_watch_action cwa
  LEFT JOIN comment c ON c.id = cwa.comment_id
  WHERE cwa.action_state = 'active'
    AND cwa.review_state <> 'reversed'                    -- 排除被推翻者（那是 ham）
    AND cwa.updated_at >= :'since'
    AND COALESCE(cwa.original_content, c.content) IS NOT NULL
),
restricted_or_flagged AS (
  -- 被限制作者 / 管理員 is_spam / 高模型分數的留言
  SELECT
    c.id                             AS comment_id,
    c.author_id,
    c.content,
    NULL::text                       AS cw_reason,
    NULL::text                       AS review_state,
    NULL::text                       AS action_state,
    c.updated_at,
    EXISTS (SELECT 1 FROM user_restriction ur WHERE ur.user_id = c.author_id) AS is_restricted,
    c.is_spam,
    c.spam_score
  FROM comment c
  WHERE c.updated_at >= :'since'
    AND c.content IS NOT NULL
    AND (
         EXISTS (SELECT 1 FROM user_restriction ur WHERE ur.user_id = c.author_id)
      OR c.is_spam = true
      OR (c.spam_score IS NOT NULL AND c.spam_score >= :spam_threshold)
    )
)
SELECT
  comment_id,
  author_id,
  content,
  1 AS label,                                            -- spam
  ARRAY_REMOVE(ARRAY[
    CASE WHEN cw_reason IS NOT NULL THEN 'community_watch:' || cw_reason END,
    CASE WHEN is_restricted THEN 'user_restriction' END,
    CASE WHEN is_spam THEN 'admin_is_spam' END,
    CASE WHEN spam_score >= :spam_threshold THEN 'model_score' END
  ], NULL) AS label_source,
  spam_score,
  updated_at
FROM (
  SELECT comment_id, author_id, content, cw_reason,
         NULL::boolean AS is_restricted, NULL::boolean AS is_spam,
         NULL::numeric AS spam_score, updated_at
  FROM cw_removed
  UNION ALL
  SELECT comment_id, author_id, content, cw_reason,
         is_restricted, is_spam, spam_score, updated_at
  FROM restricted_or_flagged
) pos

UNION ALL

-- ----------------------------------------------------------------------------
-- B. Hard-negative（label = ham, 0）—— 被推翻的處置 = 誤殺，最寶貴的 ham 訊號
--    reviewState='reversed' 或 actionState∈(restored, voided)
-- ----------------------------------------------------------------------------
SELECT
  cwa.comment_id,
  cwa.comment_author_id            AS author_id,
  COALESCE(cwa.original_content, c.content) AS content,
  0 AS label,                                            -- ham
  ARRAY['reversed_moderation'] AS label_source,
  c.spam_score,
  cwa.updated_at
FROM community_watch_action cwa
LEFT JOIN comment c ON c.id = cwa.comment_id
WHERE (cwa.review_state = 'reversed' OR cwa.action_state IN ('restored', 'voided'))
  AND cwa.updated_at >= :'since'
  AND COALESCE(cwa.original_content, c.content) IS NOT NULL;

-- ----------------------------------------------------------------------------
-- § 去識別化（匯出後處理，建議在 export job 內做，勿在此 SQL 回傳明文 author_id）
--   - author_id / comment_id → salted hash（穩定去重用、不可還原）；salt 走 secret。
--   - content：保留（模型訓練需要文字）；不另存使用者姓名/email/任何聯繫資訊。
--   - 輸出 parquet，partition by date(updated_at)，append 進 S3 訓練桶。
-- § 標籤可信度
--   - s1（守望隊未推翻）最強；s4（純模型分數）最弱，建議加 label_source 權重或抽檢。
--   - high-watermark：以本批 max(updated_at) 更新 :since，避免漏抓與重抓。
-- ============================================================================
