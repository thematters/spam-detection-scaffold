import hashlib
import math
import re
from dataclasses import asdict, dataclass
from typing import Literal


Decision = Literal["allow", "review", "block"]
SignalType = Literal[
    "external_url",
    "contact_handle",
    "obfuscated_contact",
    "adult_service",
]

LOW_CONFIDENCE_THRESHOLD = 0.35
HIGH_CONFIDENCE_THRESHOLD = 0.85
LLM_REVIEW_MODEL = "llm-review-small"
LLM_OUTPUT_TOKEN_BUDGET = 220
LLM_INPUT_USD_PER_1M_TOKENS = 0.40
LLM_OUTPUT_USD_PER_1M_TOKENS = 1.60

FIRST_PARTY_DOMAINS = {
    "matters.town",
    "matters.news",
    "matters.icu",
}


@dataclass(frozen=True)
class SpamSignal:
    type: SignalType
    value: str


@dataclass(frozen=True)
class SpamPolicyDecision:
    decision: Decision
    llm_review: bool
    reason: str
    signals: list[SpamSignal]
    llm: dict | None = None

    def to_dict(self):
        payload = {
            "decision": self.decision,
            "llmReview": self.llm_review,
            "reason": self.reason,
            "signals": [asdict(signal) for signal in self.signals],
        }
        if self.llm is not None:
            payload["llm"] = self.llm
        return payload


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u200b", "")).strip().lower()


def _normalize_contact_value(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "", raw).lower()


def estimate_token_count(text: str) -> int:
    normalized = _normalize_text(text)
    if not normalized:
        return 0
    return max(1, math.ceil(len(normalized) / 4))


def build_pattern_cache_key(text: str, signals: list[SpamSignal]) -> str:
    signal_part = "|".join(
        f"{signal.type}:{signal.value}"
        for signal in sorted(signals, key=lambda s: (s.type, s.value))
    )
    normalized = _normalize_text(text)
    compact = re.sub(r"[^\w]+", "", normalized)[:160]
    raw_key = f"{signal_part}|{compact}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def build_llm_review_plan(score: float, text: str, signals: list[SpamSignal]) -> dict:
    estimated_input_tokens = estimate_token_count(text)
    estimated_output_tokens = LLM_OUTPUT_TOKEN_BUDGET
    estimated_cost_usd = (
        estimated_input_tokens * LLM_INPUT_USD_PER_1M_TOKENS
        + estimated_output_tokens * LLM_OUTPUT_USD_PER_1M_TOKENS
    ) / 1_000_000

    return {
        "model": LLM_REVIEW_MODEL,
        "patternCacheKey": build_pattern_cache_key(text, signals),
        "estimatedInputTokens": estimated_input_tokens,
        "estimatedOutputTokens": estimated_output_tokens,
        "estimatedCostUsd": round(estimated_cost_usd, 6),
        "request": {
            "score": score,
            "signals": [asdict(signal) for signal in signals],
        },
        "responseSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["decision", "confidence", "reason", "matchedSignals"],
            "properties": {
                "decision": {"enum": ["allow", "review", "block"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string"},
                "matchedSignals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["type", "value"],
                        "properties": {
                            "type": {"type": "string"},
                            "value": {"type": "string"},
                        },
                    },
                },
            },
        },
    }


def extract_spam_signals(text: str) -> list[SpamSignal]:
    normalized = _normalize_text(text)
    signals: list[SpamSignal] = []

    for match in re.finditer(
        r"https?://[^\s)]+|(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s)]*)?",
        normalized,
    ):
        domain = (
            match.group(0)
            .replace("https://", "")
            .replace("http://", "")
            .split("/")[0]
            .removeprefix("www.")
        )
        if domain and domain not in FIRST_PARTY_DOMAINS:
            signals.append(SpamSignal(type="external_url", value=domain))

    for match in re.finditer(
        r"(?:line|telegram|tg|whatsapp|wechat|微信|賴|line\s*id)[:：\s@-]*([a-z0-9_.-]{4,})",
        normalized,
    ):
        handle = _normalize_contact_value(match.group(1))
        if handle:
            signals.append(SpamSignal(type="contact_handle", value=handle))

    for match in re.finditer(r"(?:tg|line|賴|微信)[:：\s]*((?:[a-z0-9]\s*){4,})", normalized):
        handle = _normalize_contact_value(match.group(1))
        if len(handle) >= 4:
            signals.append(SpamSignal(type="obfuscated_contact", value=handle))

    adult_terms = ("私約", "外送茶", "約妹", "上門", "包夜", "定點", "gleezy")
    if any(term in normalized for term in adult_terms):
        signals.append(SpamSignal(type="adult_service", value="matched_terms"))

    unique: dict[tuple[str, str], SpamSignal] = {}
    for signal in signals:
        unique[(signal.type, signal.value)] = signal
    return list(unique.values())


def decide_spam_policy(score: float, text: str) -> SpamPolicyDecision:
    signals = extract_spam_signals(text)
    has_high_risk_signal = any(
        signal.type in {"contact_handle", "obfuscated_contact", "adult_service"}
        for signal in signals
    )

    if score >= HIGH_CONFIDENCE_THRESHOLD and has_high_risk_signal:
        return SpamPolicyDecision(
            decision="block",
            llm_review=False,
            reason="local_model_high_confidence_with_spam_signals",
            signals=signals,
        )

    if score < LOW_CONFIDENCE_THRESHOLD and not signals:
        return SpamPolicyDecision(
            decision="allow",
            llm_review=False,
            reason="local_model_low_confidence_without_signals",
            signals=signals,
        )

    return SpamPolicyDecision(
        decision="review",
        llm_review=True,
        reason="gray_zone_or_spam_signals_require_review",
        signals=signals,
        llm=build_llm_review_plan(score, text, signals),
    )
