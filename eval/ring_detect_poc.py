"""軸一 D 原型：spam ring 偵測（template_family 跨帳號重複）。

動機：純內容打分對「開新帳號→貼重複文本」這類濫用無能為力——
2026-06-15 對 5 個已知群集實測，內容模型漏掉外送茶/翻譯社/live173（score 0.06–0.12 → allow），
但這些群集的定義訊號是「同一模板被大量新帳號重複貼」，不是文章語意。

本 POC 用**公開搜尋 API**（唯讀、不碰 DB）證明 ring 訊號抓得到內容模型漏掉的群集：
對關鍵字搜文章 → 算 template_family 指紋 → 數「同模板跨幾個不同帳號」。
正式版改吃 read-replica / L1 匯出的近期文章，按 template_family group by、count distinct author。

兩個訊號（互補）：
  (1) 近似比對 ring：char-4gram Jaccard≥0.5 union-find 連通，比 exact 模板多抓「模板有變化」的群。
  (2) 帳號名亂碼分數：數字比例/長子音串/連續數字尾 → throwaway 新帳號訊號（文字打不開時的補充）。

實測（first=30/關鍵字，繁簡都搜；exact 模板族 → 近似最大 ring 跨帳號 / 亂碼帳號比）：
  老灯闲聊            exact 3 → 近似 26 帳號 / 13%
  披着律师外衣的搅局者  exact 2 → 近似 29 帳號 / 33%
  海外中国人权律师联盟  exact 3 → 近似 26 帳號 / 10%（內容模型也 block 0.996）
  高雄翻译社          exact 10 → 近似 14 帳號 / 100% 亂碼（內容模型漏：allow 0.091）
  live173影音live秀   exact 24 → 近似仍只 4（文字變化太大）→ 實體 ring 10（共用網域/聯絡，文字打不開時的解方）/ 30% 亂碼
三訊號互補：文字型群集吃近似 ring（老灯26/披着29/海外26/高雄14），廣告型吃實體 ring（live173: 4→10），
帳號名亂碼當共同補充（高雄 100%）。正式版任一訊號達門檻即列候選，再走分級處置（見 SPAM_ROADMAP 軸一 D）。

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


def _norm(text: str) -> str:
    t = _plain(text).lower()
    t = _url.sub(" ", t); t = _h.sub(" ", t); t = _d.sub("#", t)
    return _ws.sub(" ", t).strip()


def template_family(text: str) -> str:
    """exact 模板指紋（與 prepare_article_families.py 同口徑）：遮 url/handle/數字後取前綴雜湊。"""
    return hashlib.md5(_norm(text)[:200].encode()).hexdigest()[:8]


_domain = re.compile(r"\b([a-z0-9-]+\.(?:com|net|cc|tv|xyz|top|vip|me|io|app|live|info))\b", re.I)
_contact = re.compile(
    r"(?:line|telegram|tg|whatsapp|wechat|微信|賴|skype)[\s:：@]*([a-z0-9_\-\.]{3,})", re.I)


def advertised_entities(text: str) -> set:
    """抽『廣告的同一實體』：外部網域 + 聯絡 id（line/telegram/wechat…）。
    對 live173 這種『同服務、文案每篇都變』的廣告，實體是不變量——文字近似打不開時用這個。"""
    t = _plain(text).lower()
    ents = {m.group(1).lower() for m in _domain.finditer(t)}
    ents |= {"contact:" + m.group(1).lower() for m in _contact.finditer(t)}
    return ents


def entity_top_ring(items: list) -> int:
    """共用任一廣告實體的最大跨帳號數（items: [{'content','author'}]）。"""
    ent_authors = collections.defaultdict(set)
    for a in items:
        for e in advertised_entities(a["content"]):
            ent_authors[e].add(a["author"])
    return max((len(v) for v in ent_authors.values()), default=0)


def _shingles(text: str, k: int = 4) -> set:
    t = _norm(text)
    return {t[i:i + k] for i in range(max(0, len(t) - k + 1))} if len(t) >= k else ({t} if t else set())


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def neardup_groups(texts: list, thr: float = 0.5) -> list:
    """char-4gram Jaccard ≥ thr 連成一群（union-find）。比 exact 模板多抓「模板有變化」的群。
    回傳 group index lists。"""
    sh = [_shingles(t) for t in texts]
    n = len(texts)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for i in range(n):
        for j in range(i + 1, n):
            if _jaccard(sh[i], sh[j]) >= thr:
                parent[find(i)] = find(j)
    groups = collections.defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    return list(groups.values())


def username_bot_score(u: str) -> float:
    """0~1：越高越像自動產生的 throwaway 帳號名（數字比例高、長子音串、連續數字尾）。
    對 live173 這種文字近似打不開的群，帳號名訊號是重要補充（實測高雄翻译社 100% 命中）。"""
    if not u or u == "?":
        return 0.0
    s = 0.0
    digits = sum(c.isdigit() for c in u)
    if digits / len(u) > 0.25:
        s += 0.4
    if re.search(r"[bcdfghjklmnpqrstvwxz]{5,}", u):
        s += 0.3
    if re.search(r"\d{3,}", u):
        s += 0.2
    if len(u) >= 11 and digits >= 2:
        s += 0.1
    return min(s, 1.0)


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
    return {"keyword": keyword, "articles": len(items), "authors": len(authors),
            "exact_families": len(exact_fams),
            "top_ring_accounts": top_ring, "bot_username_ratio": bot_ratio,
            "entity_top_ring": entity_top_ring(items)}


def main(argv):
    keywords = argv[1:] or ["老灯闲聊", "披着律师外衣的搅局者", "海外中国人权律师联盟",
                            "高雄翻译社", "live173影音live秀"]
    s = _client()
    print(f"{'群集':<22}{'篇':>4}{'作者':>5}{'exact模板':>9}{'近似ring':>9}{'實體ring':>9}{'亂碼帳號比':>10}")
    print("-" * 78)
    for kw in keywords:
        r = analyze(kw, fetch_cluster(s, kw))
        print(f"{kw:<22}{r['articles']:>4}{r['authors']:>5}{r['exact_families']:>9}"
              f"{r['top_ring_accounts']:>9}{r['entity_top_ring']:>9}{r['bot_username_ratio']*100:>9.0f}%")


if __name__ == "__main__":
    main(sys.argv)
