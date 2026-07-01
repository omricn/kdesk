import unittest

import base64

from tickets.broadcast_utils import (
    BROADCAST_QUICK_RECIPIENTS,
    parse_recipients,
    invalid_emails,
    body_to_html,
    sanitize_broadcast_html,
    html_text_content,
    extract_inline_images,
)

# 1x1 transparent PNG.
_PNG_B64 = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk'
            'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')


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

    def test_splits_on_bare_carriage_return(self):
        self.assertEqual(
            parse_recipients('a@x.com\rb@x.com'),
            ['a@x.com', 'b@x.com'],
        )


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


class SanitizeBroadcastHtmlTests(unittest.TestCase):
    def test_strips_script_and_event_handlers(self):
        out = sanitize_broadcast_html('<p onclick="x()">hi</p><script>alert(1)</script>')
        self.assertNotIn('<script', out)
        self.assertNotIn('onclick', out)
        self.assertIn('hi', out)

    def test_keeps_basic_formatting_and_inline_data_image(self):
        html_in = f'<p><strong>Hi</strong></p><img src="data:image/png;base64,{_PNG_B64}">'
        out = sanitize_broadcast_html(html_in)
        self.assertIn('<strong>Hi</strong>', out)
        self.assertIn('data:image/png;base64,', out)

    def test_empty_returns_empty(self):
        self.assertEqual(sanitize_broadcast_html(''), '')
        self.assertEqual(sanitize_broadcast_html(None), '')


class HtmlTextContentTests(unittest.TestCase):
    def test_strips_tags_and_collapses_whitespace(self):
        self.assertEqual(html_text_content('<p>Hello</p>\n<p>  World </p>'), 'Hello World')

    def test_image_only_body_has_no_text(self):
        html_in = f'<img src="data:image/png;base64,{_PNG_B64}">'
        self.assertEqual(html_text_content(html_in), '')

    def test_empty_returns_empty(self):
        self.assertEqual(html_text_content(''), '')
        self.assertEqual(html_text_content(None), '')


class ExtractInlineImagesTests(unittest.TestCase):
    def test_rewrites_data_uri_to_cid_and_returns_bytes(self):
        html_in = f'<p>See:</p><img src="data:image/png;base64,{_PNG_B64}" alt="x">'
        rewritten, images = extract_inline_images(html_in)
        self.assertNotIn('data:image', rewritten)
        self.assertIn('src="cid:bc-img-0"', rewritten)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]['content_id'], 'bc-img-0')
        self.assertEqual(images[0]['content_type'], 'image/png')
        self.assertEqual(images[0]['content_bytes'], base64.b64decode(_PNG_B64))

    def test_multiple_images_get_distinct_ids(self):
        one = f'<img src="data:image/png;base64,{_PNG_B64}">'
        rewritten, images = extract_inline_images(one + one)
        self.assertEqual(len(images), 2)
        self.assertIn('cid:bc-img-0', rewritten)
        self.assertIn('cid:bc-img-1', rewritten)

    def test_no_images_returns_html_unchanged(self):
        rewritten, images = extract_inline_images('<p>plain</p>')
        self.assertEqual(rewritten, '<p>plain</p>')
        self.assertEqual(images, [])

    def test_jpeg_subtype_normalized(self):
        # A tiny valid-ish jpeg payload isn't needed; base64 of arbitrary bytes decodes fine.
        payload = base64.b64encode(b'\xff\xd8\xff\xe0jpegdata').decode()
        html_in = f'<img src="data:image/jpeg;base64,{payload}">'
        _, images = extract_inline_images(html_in)
        self.assertEqual(images[0]['content_type'], 'image/jpeg')
        self.assertTrue(images[0]['name'].endswith('.jpg'))


if __name__ == '__main__':
    unittest.main()
