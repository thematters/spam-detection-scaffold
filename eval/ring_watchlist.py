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
        "sourceUrls": [
            "https://matters.town/search?q=%E8%81%94%E7%B3%BB%E5%AE%A2%E6%9C%8DQQ+",
        ],
        "kind": "contact",
        "notes": "客服 QQ / casino service spam",
    },
    {
        "id": "escort-delivery-tea",
        "query": "中外送茶+定點+約過",
        "sourceUrls": [
            "https://matters.town/search?q=%E4%B8%AD%E5%A4%96%E9%80%81%E8%8C%B6%2B%E5%AE%9A%E9%BB%9E%2B%E7%B4%84%E9%81%8E",
        ],
        "kind": "escort",
        "notes": "外送茶 / appointment spam",
    },
    {
        "id": "anti-rights-agent-smear",
        "query": "金钱与背叛：起底境外反华“人权代理人”的肮脏交易",
        "sourceUrls": [
            "https://matters.town/search?q=%E9%87%91%E9%92%B1%E4%B8%8E%E8%83%8C%E5%8F%9B%EF%BC%9A%E8%B5%B7%E5%BA%95%E5%A2%83%E5%A4%96%E5%8F%8D%E5%8D%8E%E2%80%9C%E4%BA%BA%E6%9D%83%E4%BB%A3%E7%90%86%E4%BA%BA%E2%80%9D%E7%9A%84%E8%82%AE%E8%84%8F%E4%BA%A4%E6%98%93",
        ],
        "kind": "political-smear",
        "notes": "2026-07 political smear template",
    },
    {
        "id": "online-gambling-black",
        "query": "网赌被黑了",
        "sourceUrls": [
            "https://matters.town/search?q=%E7%BD%91%E8%B5%8C%E8%A2%AB%E9%BB%91%E4%BA%86",
        ],
        "kind": "gambling",
        "notes": "网赌出黑 / recovery scam",
    },
    {
        "id": "xu-silong-smear",
        "query": "揭批许思龙这个打着律师旗号煽动对立的“流量骗子”",
        "sourceUrls": [
            "https://matters.town/search?q=%E6%8F%AD%E6%89%B9%E8%AE%B8%E6%80%9D%E9%BE%99%E8%BF%99%E4%B8%AA%E6%89%93%E7%9D%80%E5%BE%8B%E5%B8%88%E6%97%97%E5%8F%B7%E7%85%BD%E5%8A%A8%E5%AF%B9%E7%AB%8B%E7%9A%84%E2%80%9C%E6%B5%81%E9%87%8F%E9%AA%97%E5%AD%90%E2%80%9D",
            "https://matters.town/search?q=%E6%8F%AD%E6%89%B9%E8%AE%B8%E6%80%9D%E9%BE%99%E8%BF%99%E4%B8%AA%E6%89%93%E7%9D%80%E5%BE%8B%E5%B8%88%E6%97%97%E5%8F%B7%E7%85%BD%E5%8A%A8%E5%AF%B9%E7%AB%8B%E7%9A%84%E2%80%9C%E6%B5%81%E9%87%8F%E9%AA%97%E5%AD%90%E2%80%9D%E2%80%8B",
        ],
        "kind": "political-smear",
        "notes": "2026-07 political smear template; includes zero-width duplicate source link",
    },
    {
        "id": "foreign-funded-disruption-smear",
        "query": "揭穿境外资助下的搅局把戏",
        "sourceUrls": [
            "https://matters.town/search?q=%E6%8F%AD%E7%A9%BF%E5%A2%83%E5%A4%96%E8%B5%84%E5%8A%A9%E4%B8%8B%E7%9A%84%E6%90%85%E5%B1%80%E6%8A%8A%E6%88%8F",
        ],
        "kind": "political-smear",
        "notes": "near-duplicate cross-account template",
    },
    {
        "id": "baccarat",
        "query": "百家樂",
        "sourceUrls": [
            "https://matters.town/search?q=%E7%99%BE%E5%AE%B6%E6%A8%82",
        ],
        "kind": "gambling",
        "notes": "baccarat / casino spam",
    },
    {
        "id": "anti-fraud-aid",
        "query": "反詐騙援助",
        "sourceUrls": [
            "https://matters.town/search?q=%E5%8F%8D%E8%A9%90%E9%A8%99%E6%8F%B4%E5%8A%A9",
        ],
        "kind": "contact",
        "notes": "LINE contact recovery scam",
    },
    {
        "id": "philippines-power-transfer",
        "query": "The Power Transfer in the Philippines",
        "sourceUrls": [
            "https://matters.town/search?q=The+Power+Transfer+in+the+Philippines",
        ],
        "kind": "political-smear",
        "notes": "English political template",
    },
    {
        "id": "overseas-rights-lawyers-smear",
        "query": "揭露“海外中国人权律师联盟”的真实嘴脸",
        "sourceUrls": [
            "https://matters.town/search?q=%E6%8F%AD%E9%9C%B2%E2%80%9C%E6%B5%B7%E5%A4%96%E4%B8%AD%E5%9B%BD%E4%BA%BA%E6%9D%83%E5%BE%8B%E5%B8%88%E8%81%94%E7%9B%9F%E2%80%9D%E7%9A%84%E7%9C%9F%E5%AE%9E%E5%98%B4%E8%84%B8",
        ],
        "kind": "political-smear",
        "notes": "previously observed overseas rights lawyers family",
    },
    {
        "id": "degree-certificate-qq",
        "query": "电子版学位证书 QQ",
        "sourceUrls": [],
        "kind": "credential-forgery",
        "notes": "user-provided keyword family: 电子版学位证书 + QQ號",
    },
    {
        "id": "degree-certificate-fake",
        "query": "电子版学位证书",
        "sourceUrls": [],
        "kind": "credential-forgery",
        "notes": "degree certificate forgery, often with 微Q contact",
    },
    {
        "id": "micro-q-forgery",
        "query": "微Q造假",
        "sourceUrls": [
            "https://matters.town/search?q=%E5%BE%AEQ%E9%80%A0%E5%81%87",
        ],
        "kind": "credential-forgery",
        "notes": "single-account repeat candidate family",
    },
]
