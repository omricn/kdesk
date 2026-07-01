import unittest

from tickets.broadcast_utils import (
    BROADCAST_QUICK_RECIPIENTS,
    parse_recipients,
    invalid_emails,
    body_to_html,
)


class ParseRecipientsTests(unittest.TestCase):
    def test_empty_returns_empty_list(self):
        self.assertEqual(parse_recipients(''), [])
        self.assertEqual(parse_recipients(None), [])

    def test_splits_on_comma_semicolon_newline_and_trims(self):
        self.assertEqual(
            parse_recipients('a@x.com, b@x.com ;c@x.com\nd@x.com'),
            ['a@x.com', 'b@x.com', 'c@x.com', 'd@x.com'],
        )

    def test_dedupes_case_insensitively_preserving_order(self):
        self.assertEqual(parse_recipients('a@x.com, A@X.com, b@x.com'),
                         ['a@x.com', 'b@x.com'])


class InvalidEmailsTests(unittest.TestCase):
    def test_flags_malformed_addresses(self):
        self.assertEqual(
            invalid_emails(['ok@x.com', 'nope', 'a@b']),
            ['nope', 'a@b'],
        )

    def test_all_valid_returns_empty(self):
        self.assertEqual(invalid_emails(['ok@x.com', 'a.b@sub.x.co']), [])


class BodyToHtmlTests(unittest.TestCase):
    def test_blank_line_separates_paragraphs(self):
        self.assertEqual(
            body_to_html('Hello\n\nWorld'),
            '<p style="margin:0 0 16px;">Hello</p>'
            '<p style="margin:0 0 16px;">World</p>',
        )

    def test_single_newline_becomes_br(self):
        self.assertIn('Line1<br>Line2', body_to_html('Line1\nLine2'))

    def test_escapes_html_to_prevent_injection(self):
        out = body_to_html('<script>alert(1)</script>')
        self.assertNotIn('<script>', out)
        self.assertIn('&lt;script&gt;', out)

    def test_empty_body_returns_empty_string(self):
        self.assertEqual(body_to_html(''), '')
        self.assertEqual(body_to_html(None), '')


class QuickRecipientsTests(unittest.TestCase):
    def test_contains_the_two_all_employee_lists(self):
        self.assertIn('IL_All_Employees@kramerav.com', BROADCAST_QUICK_RECIPIENTS)
        self.assertIn('Global_All_Employees@kramerav.com', BROADCAST_QUICK_RECIPIENTS)


if __name__ == '__main__':
    unittest.main()
