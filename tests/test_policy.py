import unittest

from spam.policy import decide_spam_policy, extract_spam_signals


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
