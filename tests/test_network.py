import io
import sys
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from core import ReviewError
from network import sse_lines
from openai_auth import http_json, RESOURCE


class NetworkLimitsTests(unittest.TestCase):
    def test_stream_size_limit_is_cumulative_and_preserves_complete_lines(self):
        with patch('network.MAX_AI_LINE_BYTES', 8), patch('network.MAX_AI_STREAM_BYTES', 12):
            response = io.BytesIO(b'1234567\n7654321\n')
            lines = sse_lines(response)
            self.assertEqual(next(lines), b'1234567\n')
            with self.assertRaisesRegex(ReviewError, 'size limit'):
                next(lines)

    def test_long_line_is_rejected_after_a_bounded_read(self):
        with patch('network.MAX_AI_LINE_BYTES', 8):
            response = io.BytesIO(b'x' * 100)
            with self.assertRaisesRegex(ReviewError, 'size limit'):
                list(sse_lines(response))
            self.assertEqual(response.tell(), 9)

    def test_deadline_stops_a_provider_that_keeps_sending_events(self):
        with patch('network.AI_STREAM_DEADLINE', 10), patch('network.time.monotonic', side_effect=[0, 1, 11]):
            with self.assertRaisesRegex(ReviewError, 'time limit'):
                list(sse_lines(io.BytesIO(b'data: {}\n')))

    def test_provider_json_cannot_hide_trailing_data_past_the_limit(self):
        response=Mock()
        response.__enter__=Mock(return_value=io.BytesIO(b'{}' + b' ' * 10))
        response.__exit__=Mock(return_value=False)
        with patch('openai_auth.MAX_PROVIDER_JSON_BYTES', 8), patch('openai_auth.urllib.request.build_opener') as build:
            build.return_value.open.return_value=response
            with self.assertRaisesRegex(ReviewError, '4 MB limit'):
                http_json(RESOURCE + '/models')
