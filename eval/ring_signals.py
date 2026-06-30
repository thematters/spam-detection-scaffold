"""Spam ring 偵測的純訊號函式（無網路、無 DB），給 eval/ring_detect_poc.py 與
Phase 2 的 VPC runner 精修步驟共用同一份口徑。

三類跨帳號『不變量』訊號（互補）：
  1. 文字相似：template_family（exact 模板指紋）+ neardup_groups（char-4gram Jaccard）。
  2. 廣告實體：advertised_entities = 外部網域(排主流平台) + 聯絡 id + 邀請碼 token + 品牌詞。
  3. 帳號名亂碼：username_bot_score。

注意：本模組的 template_family 與 trains/spam 的 fam_hash 口徑「刻意不同」——
fam_hash 是 exact 內容雜湊（給訓練集分組去重）；本模組遮 url/handle/數字後取前綴，
是為了把『同模板但 url/碼/數字有變』的 ring 歸成一群（例如 BIN6666 與 BIN8888 同指紋）。
"""
from __future__ import annotations
import re, html, hashlib, collections

_tag = re.compile(r"<[^>]+>"); _ws = re.compile(r"\s+")
_url = re.compile(r"https?://\S+"); _h = re.compile(r"[@＠]\w+"); _d = re.compile(r"\d+")


def _plain(h: str) -> str:
    return _ws.sub(" ", _tag.sub(" ", html.unescape(h or ""))).strip()


def _norm(text: str) -> str:
    t = _plain(text).lower()
    t = _url.sub(" ", t); t = _h.sub(" ", t); t = _d.sub("#", t)
    return _ws.sub(" ", t).strip()


def template_family(text: str) -> str:
    """exact 模板指紋：遮 url/handle/數字後取前 200 字雜湊。遮數字讓不同邀請碼/金額的同模板歸同群。"""
    return hashlib.md5(_norm(text)[:200].encode()).hexdigest()[:8]


# --- 強化正規化指紋（app 層 re-cluster 用）：在 _norm 之上再吃繁簡、emoji、所有空白 ---
try:  # opencc 為選用相依（buildspec 已裝）；缺席時優雅退化（指紋仍可用，只是不吃繁簡）
    from opencc import OpenCC as _OpenCC

    _t2s = _OpenCC("t2s")

    def _to_simplified(s: str) -> str:
        return _t2s.convert(s or "")

except Exception:  # noqa: BLE001

    def _to_simplified(s: str) -> str:
        return s or ""


# 常見 emoji / 變體選擇符 / ZWJ 的 unicode 區段
_emoji = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff"
    "\U00002190-\U000021ff\U00002b00-\U00002bff️‍]"
)
_all_ws = re.compile(r"\s+")


def normalized_template(text: str) -> str:
    """比 _norm 更狠：先繁→簡，再遮 url/handle/數字、去 emoji、去掉所有空白。
    讓『同內容但繁簡／空白／emoji 不同』的貼文收斂到同一模板，避免散成多個 ring。"""
    t = _norm(_to_simplified(text))  # 繁→簡 → 小寫+去html+遮url/handle/數字+收斂空白
    t = _emoji.sub("", t)
    return _all_ws.sub("", t)  # 去掉所有空白（含換行）


def normalized_fingerprint(text: str) -> str:
    """繁簡／emoji／空白無關的模板指紋（取代 template_family 當 ring 分群鍵）。"""
    return hashlib.md5(normalized_template(text)[:200].encode()).hexdigest()[:8]


# --- 廣告實體（跨帳號不變量）---
_domain = re.compile(r"\b([a-z0-9-]+\.(?:com|net|cc|tv|xyz|top|vip|me|io|app|live|info))\b", re.I)
_contact = re.compile(
    r"(?:line|telegram|tg|whatsapp|wechat|微信|賴|skype)[\s:：@]*([a-z0-9_\-\.]{3,})", re.I)
# 邀請碼 token：crypto 返佣 ring 的真正不變量（實測 LIDANG×39 / BG998 / 3XCB / AHR99…），
# 內容模型與純網域訊號都漏（網域是交易所官網或被輪換的反向連結，碼才是 ring 共用的那個東西）。
_invite_code = re.compile(
    r"(?:邀[请請][码碼](?:是|為|为)?|invit(?:e|ation)\s*code|返佣[码碼])"
    r"\s*[:：是為为】\]\[【]*\s*([a-z0-9]{4,12})", re.I)
# 交易所/博弈品牌詞：賭場/交易所網域會輪換，品牌詞才是穩定不變量（28BET 靠這個命中，
# 而非靠被輪換的賭場網域或被當反向連結的 youtube/x）。
_brand = re.compile(
    r"(28bet|okex|okx|欧易|歐易|币安|幣安|binance|bitget|bybit|coinbase|gate\.?io|火币|火幣|huobi|kucoin|mexc)",
    re.I)
# 主流平台/縮網址：常被當 SEO 反向連結，拿來當 ring 不變量會誤殺連 YouTube/Twitter 的合法作者。
# 注意：不含 t.me（=Telegram 聯絡管道），那是 spam 訊號，要保留。
MAINSTREAM_DOMAINS = {
    "youtube.com", "youtu.be", "x.com", "twitter.com", "t.co", "pinterest.com",
    "500px.com", "facebook.com", "fb.com", "instagram.com", "medium.com",
    "github.com", "google.com", "bit.ly", "linktr.ee", "reddit.com",
    "tiktok.com", "weibo.com", "zhihu.com",
}


def advertised_entities(text: str) -> set:
    """抽『廣告的同一實體』作為跨帳號不變量：外部網域(排主流平台)、聯絡 id、邀請碼、品牌詞。
    對文案每篇都變的廣告（live173 / crypto 返佣），文字近似打不開時，實體才是不變量。"""
    t = _plain(text).lower()
    ents = {m.group(1).lower() for m in _domain.finditer(t)
            if m.group(1).lower() not in MAINSTREAM_DOMAINS}
    ents |= {"contact:" + m.group(1).lower() for m in _contact.finditer(t)}
    ents |= {"invite:" + m.group(1).upper() for m in _invite_code.finditer(t)}
    ents |= {"brand:" + m.group(1).lower().replace(".", "") for m in _brand.finditer(t)}
    return ents


def top_entity_ring(items: list):
    """回傳 (entity, 跨帳號數)：共用任一廣告實體的最大跨帳號群。items: [{'content','author'}]。"""
    ent_authors = collections.defaultdict(set)
    for a in items:
        for e in advertised_entities(a["content"]):
            ent_authors[e].add(a["author"])
    if not ent_authors:
        return (None, 0)
    e, au = max(ent_authors.items(), key=lambda kv: len(kv[1]))
    return (e, len(au))


def entity_top_ring(items: list) -> int:
    """共用任一廣告實體的最大跨帳號數（向後相容；明細用 top_entity_ring）。"""
    return top_entity_ring(items)[1]


# --- 文字近似 ring ---
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


# --- 帳號名亂碼 ---
def username_bot_score(u: str) -> float:
    """0~1：越高越像自動產生的 throwaway 帳號名（數字比例高、長子音串、連續數字尾）。"""
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
