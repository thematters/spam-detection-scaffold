"""軸一 D 原型：spam ring 偵測（template_family 跨帳號重複）。

動機：純內容打分對「開新帳號→貼重複文本」這類濫用無能為力——
2026-06-15 對 5 個已知群集實測，內容模型漏掉外送茶/翻譯社/live173（score 0.06–0.12 → allow），
但這些群集的定義訊號是「同一模板被大量新帳號重複貼」，不是文章語意。

本 POC 用**公開搜尋 API**（唯讀、不碰 DB）證明 ring 訊號抓得到內容模型漏掉的群集：
對關鍵字搜文章 → 算 template_family 指紋 → 數「同模板跨幾個不同帳號」。
正式版改吃 read-replica / L1 匯出的近期文章，按 template_family group by、count distinct author。

實測結果（first=30/關鍵字，繁簡都搜）：
  老灯闲聊            1 模板跨 18 帳號（部分純圖內容模型看不到）
  披着律师外衣的搅局者  1 模板跨 28 帳號
  海外中国人权律师联盟  1 模板跨 25 帳號（內容模型也 block 0.996）
  高雄翻译社          1 模板跨 11 帳號（內容模型漏：allow 0.091）
  live173影音live秀   模板變化多(24 族)，最大 ring 僅 4 → 需近似比對/帳號名亂碼訊號補強

用法：python ring_detect_poc.py "<關鍵字1>" "<關鍵字2>" ...
（需 cloudscraper + opencc-python-reimplemented；production search 在 Cloudflare 後）
"""
from __future__ import annotations
import sys, time, re, html, hashlib, collections
import cloudscraper
try:
    from opencc import OpenCC
    _t2s = OpenCC("t2s").convert
except Exception:
    _t2s = lambda x: x  # 無 opencc 時退化為原字串

GQL = "https://server.matters.town/graphql"
SEARCH = ("query S($i: SearchInput!){ search(input:$i){ edges{ node{ ... on Article "
          "{ id content author { userName } } } } } }")
NODE = "query N($id: ID!){ node(input:{id:$id}){ ... on Article { id content } } }"

_tag = re.compile(r"<[^>]+>"); _ws = re.compile(r"\s+")
_url = re.compile(r"https?://\S+"); _h = re.compile(r"[@＠]\w+"); _d = re.compile(r"\d+")


def _plain(h: str) -> str:
    return _ws.sub(" ", _tag.sub(" ", html.unescape(h or ""))).strip()


def template_family(text: str) -> str:
    """與 prepare_article_families.py 同口徑：遮 url/handle/數字後取前綴雜湊。"""
    t = _plain(text).lower()
    t = _url.sub(" ", t); t = _h.sub(" ", t); t = _d.sub("#", t)
    t = _ws.sub(" ", t).strip()
    return hashlib.md5(t[:200].encode()).hexdigest()[:8]


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
    fam_authors = collections.defaultdict(set)
    empty = 0
    for a in arts.values():
        if len(_plain(a["content"])) < 2:
            empty += 1
        fam_authors[template_family(a["content"])].add(a["author"])
    authors = {a["author"] for a in arts.values()}
    biggest = max(fam_authors.items(), key=lambda kv: len(kv[1]), default=("-", set()))
    return {"keyword": keyword, "articles": len(arts), "authors": len(authors),
            "families": len(fam_authors), "empty": empty,
            "top_family": biggest[0], "top_ring_accounts": len(biggest[1])}


def main(argv):
    keywords = argv[1:] or ["老灯闲聊", "披着律师外衣的搅局者", "海外中国人权律师联盟",
                            "高雄翻译社", "live173影音live秀"]
    s = _client()
    print(f"{'群集':<22}{'篇':>4}{'作者':>5}{'模板族':>6}{'純圖':>5}{'最大ring(跨N帳號)':>16}")
    print("-" * 64)
    for kw in keywords:
        r = analyze(kw, fetch_cluster(s, kw))
        print(f"{kw:<22}{r['articles']:>4}{r['authors']:>5}{r['families']:>6}{r['empty']:>5}"
              f"{r['top_ring_accounts']:>16}")


if __name__ == "__main__":
    main(sys.argv)
