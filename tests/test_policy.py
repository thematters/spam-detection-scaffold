import unittest

from spam.policy import (
    build_pattern_cache_key,
    decide_spam_policy,
    estimate_token_count,
    extract_spam_signals,
)


class SpamPolicyTest(unittest.TestCase):
    def test_allows_low_score_without_signals(self):
        decision = decide_spam_policy(0.1, "這是一篇正常的讀書心得")

        self.assertEqual(decision.decision, "allow")
        self.assertFalse(decision.llm_review)
        self.assertEqual(decision.signals, [])

    def test_blocks_high_score_with_contact_signal(self):
        decision = decide_spam_policy(0.95, "下班找妹妹放鬆 TG: abc1234")

        self.assertEqual(decision.decision, "block")
        self.assertFalse(decision.llm_review)
        self.assertEqual(decision.signals[0].type, "contact_handle")

    def test_reviews_gray_zone(self):
        decision = decide_spam_policy(0.5, "內容有點可疑")

        self.assertEqual(decision.decision, "review")
        self.assertTrue(decision.llm_review)
        self.assertIsNotNone(decision.llm)
        self.assertEqual(decision.llm["request"]["score"], 0.5)
        self.assertEqual(
            decision.llm["responseSchema"]["required"],
            ["decision", "confidence", "reason", "matchedSignals"],
        )
        self.assertGreater(decision.llm["estimatedInputTokens"], 0)
        self.assertGreater(decision.llm["estimatedCostUsd"], 0)

    def test_pattern_cache_key_is_stable_for_same_pattern(self):
        text = "聯絡 TG: abc1234 看詳情 https://spam.example"
        signals = extract_spam_signals(text)

        self.assertEqual(
            build_pattern_cache_key(text, signals),
            build_pattern_cache_key("聯絡  TG：abc1234 看詳情 https://spam.example", signals),
        )

    def test_estimates_tokens_without_external_llm_call(self):
        self.assertEqual(estimate_token_count(""), 0)
        self.assertEqual(estimate_token_count("abcd"), 1)
        self.assertEqual(estimate_token_count("abcde"), 2)

    def test_extracts_obfuscated_contact(self):
        signals = extract_spam_signals("聯繫晚晚 TG：w n 9 9 2 2")

        self.assertIn(
            ("obfuscated_contact", "wn9922"),
            [(s.type, s.value) for s in signals],
        )

    def test_ignores_first_party_domains(self):
        signals = extract_spam_signals("請看 https://matters.town/a/example 和 https://spam.example")

        self.assertEqual([s.value for s in signals], ["spam.example"])


if __name__ == "__main__":
    unittest.main()
