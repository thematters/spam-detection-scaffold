"""Read-only production-search watchlist for recurring spam-ring families.

These are not enforcement rules. The formal detector is still the replica-backed
ring job. This list gives operators a stable set of live search probes for
checking whether known families remain visible and whether ring_signals still
extract useful invariants from them.
"""

WATCHLIST = [
    {
        "id": "qq-customer-service",
        "query": "联系客服QQ",
        "kind": "contact",
        "notes": "客服 QQ / casino service spam",
    },
    {
        "id": "escort-delivery-tea",
        "query": "中外送茶+定點+約過",
        "kind": "escort",
        "notes": "外送茶 / appointment spam",
    },
    {
        "id": "anti-rights-agent-smear",
        "query": "金钱与背叛：起底境外反华“人权代理人”的肮脏交易",
        "kind": "political-smear",
        "notes": "2026-07 political smear template",
    },
    {
        "id": "online-gambling-black",
        "query": "网赌被黑了",
        "kind": "gambling",
        "notes": "网赌出黑 / recovery scam",
    },
    {
        "id": "xu-silong-smear",
        "query": "揭批许思龙这个打着律师旗号煽动对立的“流量骗子”",
        "kind": "political-smear",
        "notes": "2026-07 political smear template",
    },
    {
        "id": "foreign-funded-disruption-smear",
        "query": "揭穿境外资助下的搅局把戏",
        "kind": "political-smear",
        "notes": "near-duplicate cross-account template",
    },
    {
        "id": "baccarat",
        "query": "百家樂",
        "kind": "gambling",
        "notes": "baccarat / casino spam",
    },
    {
        "id": "anti-fraud-aid",
        "query": "反詐騙援助",
        "kind": "contact",
        "notes": "LINE contact recovery scam",
    },
    {
        "id": "philippines-power-transfer",
        "query": "The Power Transfer in the Philippines",
        "kind": "political-smear",
        "notes": "English political template",
    },
    {
        "id": "overseas-rights-lawyers-smear",
        "query": "揭露“海外中国人权律师联盟”的真实嘴脸",
        "kind": "political-smear",
        "notes": "previously observed overseas rights lawyers family",
    },
    {
        "id": "degree-certificate-fake",
        "query": "电子版学位证书",
        "kind": "credential-forgery",
        "notes": "degree certificate forgery, often with 微Q contact",
    },
    {
        "id": "micro-q-forgery",
        "query": "微Q造假",
        "kind": "credential-forgery",
        "notes": "single-account repeat candidate family",
    },
]

