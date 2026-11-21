"""Check actual G3 draft structure without treating it as content approval."""
import unittest
import check_storyboard_draft as draft


def shot(name):
    return '### 镜头' + name + '｜近景\n' + '\n'.join(
        label + '：' + ('—' if label in ('台词', '重音', '人声') else '实际内容')
        for label in draft.REQUIRED) + '\n'


class DraftTests(unittest.TestCase):
    def test_populated_multiple_shots_and_non_applicable_values(self):
        result = draft.check_text(shot('A01') + shot('A02'))
        self.assertTrue(result['draft_structure_passed'], result)
        self.assertEqual(result['shots_checked'], 2)

    def test_timing_reports_original_and_revised_17_shots(self):
        ticket = '乙（现场，甲听见）：十七。我就拿了我这件。<br>甲（现场，乙听见）：巧了，我也是。'
        red = '乙（现场，甲听见）：那你看看内袋。我在拉链头上绑了根红线。'

        def timed(name, seconds, dialogue):
            return shot(name).replace('｜近景', f'｜近景｜{seconds} 秒').replace('台词：—', '台词：' + dialogue)

        before = draft.check_text(timed('04', 4.5, ticket) + timed('07', 6, red))
        self.assertTrue(before['draft_structure_passed'])
        self.assertEqual(
            [(item['dialogue_chars'], item['remaining_seconds'], item['status'])
             for item in before.get('timing', [])],
            [(18, 0.0, 'dialogue_fills_shot'), (19, 1.25, 'little_time_left')],
        )
        after = draft.check_text(timed('04', 5.5, ticket) + timed('07', 7.5, red))
        self.assertEqual(
            [(item['remaining_seconds'], item['status']) for item in after['timing']],
            [(1.0, 'little_time_left'), (2.75, 'review_timing')],
        )

    def test_timing_handles_multiline_dialogue_and_alternative_rate(self):
        text = shot('01').replace('｜近景', '｜近景｜9秒')
        text = text.replace('台词：—', '台词：\n甲：这边。\n乙：看到了。')
        result = draft.check_text(text, dialogue_rate=2.5)
        self.assertEqual(result['timing'][0]['dialogue_chars'], 7)
        self.assertEqual(result['timing'][0]['dialogue_seconds'], 2.8)

    def test_timing_skips_silence_and_requests_manual_check_for_ambiguous_input(self):
        def timed(name, dialogue):
            return shot(name).replace('｜近景', '｜近景｜5秒').replace('台词：—', '台词：' + dialogue)

        result = draft.check_text(timed('01', '无') + timed('02', '甲：OK，17号。'))
        self.assertEqual([item['status'] for item in result['timing']], ['manual_check'])
        self.assertIsNone(result['timing'][0].get('dialogue_seconds'))

    def test_inline_turns_with_voiceover_and_addressed_dialogue(self):
        cases = [
            ('01', 9, '甲（OS，仅观众听见）：东西应该还在这里吧。乙，对甲说：你找到钥匙了吗？甲，对乙说：没有。', 21, 3.75),
            ('02', 11, '乙，对甲说：要不先检查桌下面？甲（OS，仅观众听见）：桌底光线不够……我得找个手电筒照一下才能看得清楚。', 34, 2.5),
            ('03', 12, '乙，对甲说：别急。柜子看过了吗？甲（OS，仅观众听见）：抽屉里有个纸袋，钥匙说不定就在里面。甲，对乙说：我再仔细找一遍吧。', 37, 2.75),
        ]
        for name, seconds, dialogue, chars, remaining in cases:
            with self.subTest(shot=name):
                text = shot(name).replace('｜近景', f'｜近景｜{seconds}秒')
                text = text.replace('台词：—', '台词：' + dialogue)
                result = draft.check_text(text)['timing'][0]
                self.assertEqual(
                    (result.get('dialogue_chars'), result.get('remaining_seconds'), result['status']),
                    (chars, remaining, 'review_timing'),
                )

    def test_inline_turn_with_overlap_or_unclear_colon_needs_manual_estimate(self):
        for dialogue in ('甲：这边。乙（同时说）：我知道。', '甲：我只想说：先别走。'):
            with self.subTest(dialogue=dialogue):
                text = shot('01').replace('｜近景', '｜近景｜9秒')
                text = text.replace('台词：—', '台词：' + dialogue)
                self.assertEqual(draft.check_text(text)['timing'][0]['status'], 'manual_check')

    def test_missing_field_in_second_shot_is_rejected(self):
        result = draft.check_text(shot('01') + shot('02').replace('光影：实际内容', '光影：'))
        self.assertFalse(result['draft_structure_passed'])
        self.assertIn('光影', result['errors'][0])

    def test_empty_template_and_missing_heading_are_rejected(self):
        self.assertFalse(draft.check_text('# 分镜\n场景：待填写')['draft_structure_passed'])
        self.assertFalse(draft.check_text(shot('01').replace('站位：实际内容', '站位：待填写'))['draft_structure_passed'])

    def test_parenthesized_and_unrecognized_headings_are_not_skipped(self):
        incomplete = '## 镜头 02（近景）\n场景：卧室\n'
        result = draft.check_text(shot('01') + incomplete)
        self.assertFalse(result['draft_structure_passed'])
        self.assertEqual(result['shots_checked'], 2)
        self.assertIn('光影', result['errors'][0])
        self.assertFalse(draft.check_text(shot('01') + '## 镜头 ???\n场景：卧室\n')['draft_structure_passed'])

    def test_timing_accepts_estimated_and_seconds_aliases(self):
        for suffix in ('｜预计4秒', '｜约4.0秒', '｜4s'):
            with self.subTest(suffix=suffix):
                text = shot('01').replace('｜近景', '｜近景' + suffix).replace('台词：—', '台词：甲：你好啊。')
                timing = draft.check_text(text)['timing']
                self.assertEqual(len(timing), 1)
                self.assertEqual(timing[0]['shot_seconds'], 4.0)
                self.assertNotEqual(timing[0]['status'], 'manual_check')


if __name__ == '__main__':
    unittest.main()
