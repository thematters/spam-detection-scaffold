-- ============================================================================
-- 文章「可公開資料集」抽取（含 license）—— 供 matters-spam-dataset 發佈層使用。
-- 一次掃描輸出每篇文章的「所有訊號」，標籤政策留給 Python（build_release.py）裁決，
-- 這樣同一份抽取可組出不同品質層的釋出版本，且 license 一併帶出供法務閘判斷。
--
-- ⚠️ 在 READ-REPLICA 上跑（VPC 內）。輸出後經 select_publishable（CC 閘）+ deidentify 再發佈。
-- ⚠️ schema：article → article_version_newest(av) → article_content(ac)；license 在 av。
--    license enum：cc_0 | cc_by_nc_nd_2 | cc_by_nc_nd_4 | arr（paywall 強制 arr）。
--
-- 參數：
--   :since           增量起點（updated_at high-watermark）
--   :spam_threshold  模型分數視為 spam 的門檻（與線上對齊，預設 0.80）
--   :minlen          內容最小長度（過濾純圖/極短，降模型誤判污染）
-- ----------------------------------------------------------------------------
\set since '2020-01-01'
\set spam_threshold 0.80
\set minlen 1

SELECT
  a.id                              AS article_id,
  a.author_id,
  av.title,
  -- 去 HTML，標題 + 內文合併（與 acceptance / 訓練抽取一致）
  regexp_replace(
    coalesce(av.title,'') || ' ' || coalesce(ac.content,''),
    '<[^>]+>', ' ', 'g'
  )                                 AS content,
  av.license,                       -- ← 法務閘關鍵：ham 只收 CC（cc_*），arr 擋
  a.is_spam,                        -- 管理員 setSpamStatus（最權威，可為 NULL）
  EXISTS (SELECT 1 FROM user_restriction ur WHERE ur.user_id = a.author_id)
                                    AS is_restricted,   -- 代理正樣本（已知噪音，弱）
  a.spam_score,                     -- 模型分數（最弱）
  a.created_at,
  a.updated_at
FROM article a
JOIN article_version_newest av ON av.article_id = a.id
JOIN article_content        ac ON ac.id = av.content_id
WHERE a.state = 'active'
  AND a.updated_at >= :'since'
  AND length(regexp_replace(
        coalesce(av.title,'') || ' ' || coalesce(ac.content,''), '<[^>]+>', ' ', 'g'
      )) >= :minlen
  -- 候選池：任一 spam 訊號（供正樣本）或 CC 授權（供乾淨 ham）。arr 非 spam 者排除。
  AND (
        a.is_spam = true
     OR EXISTS (SELECT 1 FROM user_restriction ur WHERE ur.user_id = a.author_id)
     OR (a.spam_score IS NOT NULL AND a.spam_score >= :spam_threshold)
     OR av.license IN ('cc_0', 'cc_by_nc_nd_2', 'cc_by_nc_nd_4')
  );

-- ----------------------------------------------------------------------------
-- § 後處理（build_release.py）：
--   1. 標籤政策（預設權威優先）：
--        spam ← human_relabel=spam（矯正 CSV）或 is_spam=true
--        ham  ← human_relabel=ham（矯正 CSV）
--        --include-weak 才納入 is_restricted / spam_score≥門檻（label_weight 壓低）
--   2. select_publishable：spam 全收；ham 只收 license ∈ cc_*。
--   3. deidentify：id 重鍵 + 聯絡方式佔位符 + 時間粗化。
--   author_id / article_id 切勿原樣落地，去識別在 export job 內完成。
-- ============================================================================
