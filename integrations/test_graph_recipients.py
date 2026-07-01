import unittest

from integrations.graph_client import _as_recipients


class AsRecipientsTests(unittest.TestCase):
    def test_string_becomes_single_recipient(self):
        self.assertEqual(
            _as_recipients('a@x.com'),
            [{'emailAddress': {'address': 'a@x.com'}}],
        )

    def test_list_becomes_multiple_recipients(self):
        self.assertEqual(
            _as_recipients(['a@x.com', 'b@x.com']),
            [
                {'emailAddress': {'address': 'a@x.com'}},
                {'emailAddress': {'address': 'b@x.com'}},
            ],
        )

    def test_empty_values_yield_empty_list(self):
        self.assertEqual(_as_recipients(''), [])
        self.assertEqual(_as_recipients(None), [])
        self.assertEqual(_as_recipients([]), [])

    def test_drops_empty_entries_in_list(self):
        self.assertEqual(
            _as_recipients(['a@x.com', '', None]),
            [{'emailAddress': {'address': 'a@x.com'}}],
        )


if __name__ == '__main__':
    unittest.main()
