-- 軸一 D（動態版）— moment spam ring 偵測（讀 read-replica）
--
-- 用途：抓「開新帳號→貼重複動態」群集（純內容打分擋不住，與文章 ring 同一現象）。
-- 這是 DB 端「粗篩第一關」：按正規化模板指紋 group by、count distinct 作者、過濾新帳號。
-- app 層（ring_detect_poc.py 的近似比對 char-4gram Jaccard + 廣告實體 + 帳號名亂碼）再對候選精修。
--
-- 與文章版（detect_spam_rings.sql）的差異：
--   moment.content 是「單一欄位」短內容，不像 article 要 join article_version_newest/article_content。
--   故查詢更簡單、更快。其餘正規化口徑（遮 url/handle/數字、md5 前綴）與文章版一致以跨層去重。
--
-- 執行：須在 VPC 內（VPC runner）連 matters-prod-replica-analysis；GitHub-hosted runner 連不到。唯讀。
-- 參數：:days 近期窗（如 30）、:min_authors 同模板最少跨幾個帳號（如 3）、:new_account_days 新帳號門檻（如 30）。

WITH recent_moments AS (
  SELECT
    m.id,
    m.author_id,
    m.created_at,
    -- 正規化模板指紋（與文章版同口徑）：strip html → 遮 url → 遮 @handle → 遮數字 → 前 200 字 md5
    md5(left(
      regexp_replace(
        regexp_replace(
          regexp_replace(
            regexp_replace(lower(coalesce(m.content, '')),
                           '<[^>]+>', ' ', 'g'),          -- strip html
            'https?://[^[:space:]]+', ' ', 'g'),          -- mask url
          '[@＠][[:alnum:]_]+', ' ', 'g'),                -- mask handle
        '[0-9]+', '#', 'g'),                              -- mask digits
      200)) AS template_fam
  FROM moment m
  WHERE m.created_at >= now() - (:days || ' days')::interval
    AND m.state = 'active'
),
rings AS (
  SELECT
    rm.template_fam,
    count(*)                                            AS n_moments,
    count(DISTINCT rm.author_id)                        AS n_authors,
    -- 這群作者裡「新帳號」（建立未滿 :new_account_days 天）的比例
    avg((u.created_at >= now() - (:new_account_days || ' days')::interval)::int) AS new_account_ratio,
    array_agg(DISTINCT u.user_name) FILTER (WHERE u.user_name IS NOT NULL)       AS sample_authors,
    -- 下游「標記帳號為 spam」用：候選 ring 的全部作者 id（去重）
    array_agg(DISTINCT rm.author_id)                    AS author_ids,
    array_agg(DISTINCT rm.id)                           AS moment_ids,  -- 該群動態（app 層抓 content 做精修）
    min(u.created_at)                                   AS earliest_account,
    max(rm.created_at)                                  AS latest_post
  FROM recent_moments rm
  -- 只算「還沒處理」的作者：排除已 frozen/banned/archived 的帳號（避免凍結後同批人反覆成新 ring、重工）
  JOIN "user" u ON u.id = rm.author_id
                AND u.state NOT IN ('banned', 'frozen', 'archived')
  GROUP BY rm.template_fam
)
SELECT
  template_fam,
  n_moments,
  n_authors,
  round(new_account_ratio::numeric, 2) AS new_account_ratio,
  (sample_authors)[1:10]               AS sample_authors,
  author_ids,
  moment_ids,
  earliest_account,
  latest_post
FROM rings
WHERE n_authors >= :min_authors            -- 同模板跨 ≥K 個不同帳號 = ring 訊號
ORDER BY n_authors DESC, n_moments DESC;

-- 注意：本查詢只「找候選 ring」，不做任何處置（體檢階段）。處置分級（排除 discovery + 標記帳號 spam
-- vs 人工佇列）在 app 層，須符合 SPAM_ROADMAP 軸一 D 的安全護欄：雙鑰、可逆+可申訴、老帳號豁免、影子先行。
