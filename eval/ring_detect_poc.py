"""軸一 D 原型：spam ring 偵測（template_family 跨帳號重複）。

動機：純內容打分對「開新帳號→貼重複文本」這類濫用無能為力——
2026-06-15 對 5 個已知群集實測，內容模型漏掉外送茶/翻譯社/live173（score 0.06–0.12 → allow），
但這些群集的定義訊號是「同一模板被大量新帳號重複貼」，不是文章語意。

本 POC 用**公開搜尋 API**（唯讀、不碰 DB）證明 ring 訊號抓得到內容模型漏掉的群集：
對關鍵字搜文章 → 算 template_family 指紋 → 數「同模板跨幾個不同帳號」。
正式版改吃 read-replica / L1 匯出的近期文章，按 template_family group by、count distinct author。

訊號（互補，純函式抽到 eval/ring_signals.py，與 Phase 2 VPC runner 共用）：
  (1) 近似比對 ring：char-4gram Jaccard≥0.5 union-find 連通，比 exact 模板多抓「模板有變化」的群。
  (2) 廣告實體 ring：外部網域(排主流平台) + 聯絡 id + **邀請碼 token** + **品牌詞**；文字近似打不開時的解方。
  (3) 帳號名亂碼分數：數字比例/長子音串/連續數字尾 → throwaway 新帳號訊號。

2026-06-19 擴充（軸一 D 正式版前置，補 crypto 返佣 / 博弈品牌破口）：
  邀請碼 token（实测 币安 LIDANG×39、Bitget BG998/3XCB）與品牌詞（28BET）原本漏抓——
  邀請碼是返佣 ring 的真正不變量、品牌詞是博弈 ring 的真正不變量（賭場網域會輪換）；
  並把 youtube/x/pinterest 等主流平台網域排出實體集合（28BET 原本誤 key 在這些反向連結上）。

用法：python ring_detect_poc.py "<關鍵字1>" "<關鍵字2>" ...
（需 cloudscraper + opencc-python-reimplemented；production search 在 Cloudflare 後）
"""
from __future__ import annotations
import sys, time
import cloudscraper
from ring_signals import (
    _plain, template_family, neardup_groups, username_bot_score,
    top_entity_ring,
)
try:
    from opencc import OpenCC
    _t2s = OpenCC("t2s").convert
except Exception:
    _t2s = lambda x: x  # 無 opencc 時退化為原字串

GQL = "https://server.matters.town/graphql"
SEARCH = ("query S($i: SearchInput!){ search(input:$i){ edges{ node{ ... on Article "
          "{ id content author { userName } } } } } }")
NODE = "query N($id: ID!){ node(input:{id:$id}){ ... on Article { id content } } }"


def _client():
    s = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "darwin", "mobile": False})
    s.headers.update({"Content-Type": "application/json", "x-client-name": "spam-ring-poc"})
    return s


def _gql(s, q, v):
    b = s.post(GQL, json={"query": q, "variables": v}, timeout=60).json()
    if b.get("errors"):
        raise RuntimeError(str(b["errors"])[:120])
    return b["data"]


def fetch_cluster(s, keyword: str, first: int = 30) -> dict:
    """繁簡都搜、正文空的用 node 補抓。回 {article_id: {content, author}}。"""
    arts: dict = {}
    forms = [keyword] + ([_t2s(keyword)] if _t2s(keyword) != keyword else [])
    for key in forms:
        try:
            data = _gql(s, SEARCH, {"i": {"key": key, "type": "Article", "first": first, "record": False}})
        except Exception as e:
            print(f"  [搜尋失敗 {key}] {e}"); continue
        for e in data["search"]["edges"]:
            n = e.get("node") or {}
            if not n.get("id"):
                continue
            content = n.get("content") or ""
            if len(_plain(content)) < 2:
                try:
                    content = (_gql(s, NODE, {"id": n["id"]})["node"] or {}).get("content") or content
                except Exception:
                    pass
            arts[n["id"]] = {"content": content, "author": (n.get("author") or {}).get("userName") or "?"}
        time.sleep(0.5)
    return arts


def analyze(keyword: str, arts: dict) -> dict:
    items = list(arts.values())
    texts = [a["content"] for a in items]
    authors = {a["author"] for a in items}
    exact_fams = {template_family(a["content"]) for a in items}
    # 近似群：char-4gram Jaccard ≥0.5 連通；每群數不同作者，取最大 ring
    top_ring = 0
    for idxs in neardup_groups(texts):
        top_ring = max(top_ring, len({items[i]["author"] for i in idxs}))
    bot = [username_bot_score(a["author"]) for a in items]
    bot_ratio = sum(1 for b in bot if b >= 0.4) / len(bot) if bot else 0.0
    top_ent, ent_ring = top_entity_ring(items)
    return {"keyword": keyword, "articles": len(items), "authors": len(authors),
            "exact_families": len(exact_fams),
            "top_ring_accounts": top_ring, "bot_username_ratio": bot_ratio,
            "entity_top_ring": ent_ring, "top_entity": top_ent or "-"}


def main(argv):
    keywords = argv[1:] or ["老灯闲聊", "披着律师外衣的搅局者", "海外中国人权律师联盟",
                            "高雄翻译社", "live173影音live秀"]
    s = _client()
    print(f"{'群集':<22}{'篇':>4}{'作者':>5}{'exact模板':>9}{'近似ring':>9}{'實體ring':>9}{'亂碼帳號比':>10}  最強實體")
    print("-" * 92)
    for kw in keywords:
        r = analyze(kw, fetch_cluster(s, kw))
        print(f"{kw:<22}{r['articles']:>4}{r['authors']:>5}{r['exact_families']:>9}"
              f"{r['top_ring_accounts']:>9}{r['entity_top_ring']:>9}{r['bot_username_ratio']*100:>9.0f}%  {r['top_entity']}")


if __name__ == "__main__":
    main(sys.argv)
