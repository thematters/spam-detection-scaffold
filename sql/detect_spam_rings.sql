-- 軸一 D — spam ring 偵測（資料源版，讀 read-replica）
--
-- 用途：抓「開新帳號→貼重複文本」群集（純內容打分擋不住，見 eval/ring_detect_poc.py 實證）。
-- 這是 DB 端的「粗篩第一關」：按正規化模板指紋 group by、count distinct 作者、過濾新帳號。
-- app 層（ring_detect_poc.py 的近似比對 + 帳號名亂碼訊號）再對候選做精修。
--
-- 執行：須在 VPC 內（VPC Lambda / VPC runner）連 matters-prod-replica-analysis；
--       GitHub-hosted runner 連不到（見 SPAM_ROADMAP「依賴/待外部」）。唯讀。
-- 參數：:days 近期窗（如 30）、:min_authors 同模板最少跨幾個帳號（如 3）、:new_account_days 新帳號門檻（如 30）。
-- schema 對齊 extract_training_data.sql：article → article_version_newest → article_content；"user"。

WITH recent_articles AS (
  SELECT
    a.id,
    a.author_id,
    a.created_at,
    -- 正規化模板指紋：去 HTML 標籤近似（strip <...>）→ 遮 URL → 遮 @handle → 遮數字 → 取前 200 字雜湊
    md5(left(
      regexp_replace(
        regexp_replace(
          regexp_replace(
            regexp_replace(lower(coalesce(ac.content,'')),
                           '<[^>]+>', ' ', 'g'),          -- strip html
            'https?://[^[:space:]]+', ' ', 'g'),          -- mask url
          '[@＠][[:alnum:]_]+', ' ', 'g'),                -- mask handle
        '[0-9]+', '#', 'g'),                              -- mask digits
      200)) AS template_fam
  FROM article a
  JOIN article_version_newest av ON av.article_id = a.id
  JOIN article_content ac        ON ac.id = av.content_id
  WHERE a.created_at >= now() - (:days || ' days')::interval
    AND a.state = 'active'
),
rings AS (
  SELECT
    ra.template_fam,
    count(*)                                            AS n_articles,
    count(DISTINCT ra.author_id)                        AS n_authors,
    -- 這群作者裡「新帳號」（建立未滿 :new_account_days 天）的比例
    avg((u.created_at >= now() - (:new_account_days || ' days')::interval)::int) AS new_account_ratio,
    array_agg(DISTINCT u.user_name) FILTER (WHERE u.user_name IS NOT NULL)       AS sample_authors,
    array_agg(DISTINCT ra.author_id)                    AS author_ids,   -- 完整成員（給 app 層 upsert 用）
    array_agg(DISTINCT ra.id)                           AS article_ids,  -- 該群文章（app 層抓內容做精修）
    min(u.created_at)                                   AS earliest_account,
    max(ra.created_at)                                  AS latest_post
  FROM recent_articles ra
  -- 只算「還沒處理」的作者：排除已 frozen/banned/archived 的帳號。否則凍結一個 ring 後，
  -- 那批人仍以同模板被重新 group 成「新」候選 ring → 反覆冒出、重工（console 回報）。
  JOIN "user" u ON u.id = ra.author_id
                AND u.state NOT IN ('banned', 'frozen', 'archived')
  GROUP BY ra.template_fam
)
SELECT
  template_fam,
  n_articles,
  n_authors,
  round(new_account_ratio::numeric, 2) AS new_account_ratio,
  (sample_authors)[1:10]               AS sample_authors,
  author_ids,
  article_ids,
  earliest_account,
  latest_post
FROM rings
WHERE n_authors >= :min_authors            -- 同模板跨 ≥K 個不同帳號 = ring 訊號
   -- F1a（SPEC_RING_V2）：單帳號洗文——同一帳號同模板 ≥N 篇也成候選（送控制台，永不自動凍結）
   OR (n_authors = 1 AND n_articles >= :single_author_min_posts)
ORDER BY n_authors DESC, n_articles DESC;

-- 注意：本查詢只「找候選 ring」，不做任何處置。處置分級（凍結 vs 人工佇列）在 app 層，
-- 須符合 SPAM_ROADMAP 軸一 D 的安全護欄：雙鑰、可逆+可申訴、老帳號豁免、影子先行。
