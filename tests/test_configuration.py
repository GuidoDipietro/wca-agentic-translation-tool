import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from configuration import env_value, checked_token
from connections import Connections
from core import ReviewError
from weblate import load_token


class ConfigurationTests(unittest.TestCase):
    def test_clients_share_literal_parser_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            path = Path(temporary) / 'configuration'
            path.write_text('export WEBLATE_TOKEN="literal-$(not-executed)"\nOPENAI_MODEL="model-name"\n')
            connections = Connections(path)
            self.assertEqual(connections.get('WEBLATE_TOKEN'), load_token(path))
            self.assertEqual(load_token(path), 'literal-$(not-executed)')
            self.assertEqual(env_value('OPENAI_MODEL', path), 'model-name')
            with patch.dict(os.environ, {'WEBLATE_TOKEN': 'environment-value'}):
                self.assertEqual(load_token(path), 'environment-value')
                self.assertEqual(connections.get('WEBLATE_TOKEN'), 'environment-value')

    def test_malformed_quotes_and_invalid_values_never_expose_the_value(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            path = Path(temporary) / 'configuration'
            path.write_text('WEBLATE_TOKEN="fictional-private-value\n')
            for read in (lambda: load_token(path), lambda: Connections(path).get('WEBLATE_TOKEN')):
                with self.assertRaises(ReviewError) as caught:
                    read()
                self.assertNotIn('fictional-private-value', str(caught.exception))
        with self.assertRaises(ReviewError) as caught:
            checked_token('fictional-private\rvalue', 'OPENAI_API_KEY')
        self.assertIn('OPENAI_API_KEY', str(caught.exception))
        self.assertNotIn('fictional-private', str(caught.exception))
