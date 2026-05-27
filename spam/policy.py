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

    def to_dict(self):
        return {
            "decision": self.decision,
            "llmReview": self.llm_review,
            "reason": self.reason,
            "signals": [asdict(signal) for signal in self.signals],
        }


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u200b", "")).strip().lower()


def _normalize_contact_value(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "", raw).lower()


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
    )
