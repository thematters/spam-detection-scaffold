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
            regexp_replace(lower(coalesce(av.title,'') || ' ' || coalesce(ac.content,'')),
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
    min(u.created_at)                                   AS earliest_account,
    max(ra.created_at)                                  AS latest_post
  FROM recent_articles ra
  JOIN "user" u ON u.id = ra.author_id
  GROUP BY ra.template_fam
)
SELECT
  template_fam,
  n_articles,
  n_authors,
  round(new_account_ratio::numeric, 2) AS new_account_ratio,
  (sample_authors)[1:10]               AS sample_authors,
  earliest_account,
  latest_post
FROM rings
WHERE n_authors >= :min_authors            -- 同模板跨 ≥K 個不同帳號 = ring 訊號
ORDER BY n_authors DESC, n_articles DESC;

-- 注意：本查詢只「找候選 ring」，不做任何處置。處置分級（凍結 vs 人工佇列）在 app 層，
-- 須符合 SPAM_ROADMAP 軸一 D 的安全護欄：雙鑰、可逆+可申訴、老帳號豁免、影子先行。
