import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

import requests

import gen


class CardTests(unittest.TestCase):
    def setUp(self):
        sample = json.loads((gen.ROOT / "examples" / "sample-player.json").read_text(encoding="utf-8"))
        self.player = sample["player"]
        self.scoreboard = sample["scoreboard"]

    def test_missing_token_is_actionable(self):
        with self.assertRaisesRegex(ValueError, "Set OC_TOKEN"):
            gen.validate_token(None)

    def test_token_format_is_validated_without_echoing_secret(self):
        secret = "not-a-valid-secret"
        with self.assertRaises(ValueError) as caught:
            gen.validate_token(secret)
        self.assertNotIn(secret, str(caught.exception))
        self.assertEqual(gen.validate_token(" 12345678-1234-1234-1234-123456789abc "),
                         "12345678-1234-1234-1234-123456789abc")

    def test_unranked_player(self):
        self.assertEqual(gen.find_scoreboard_position(self.player, []), "N/A")
        self.assertEqual(gen.find_scoreboard_position(self.player, self.scoreboard), 1)

    def test_no_solves_and_no_categories_are_supported(self):
        for categories in ({}, {"web": {"solves": 0}}):
            self.player["categories"] = categories
            self.player["correctSubmissions"] = 0
            self.assertEqual(gen.find_best_category(self.player), "N/A")
            counts = {name: value["solves"] for name, value in categories.items()}
            for style in gen.TEMPLATE_COLOR_PALETTES:
                self.assertEqual(gen.generate_histogram_svg(counts, style), "")
                ET.fromstring(gen.make_card(gen.summarize_player(self.player, []), counts, style))

    def test_all_themes_render_as_valid_xml(self):
        data = gen.summarize_player(self.player, self.scoreboard)
        categories = {name: value["solves"] for name, value in self.player["categories"].items()}
        for style in gen.TEMPLATE_COLOR_PALETTES:
            with self.subTest(style=style):
                card = gen.make_card(data, categories, style)
                self.assertEqual(ET.fromstring(card).tag, "{http://www.w3.org/2000/svg}svg")
                self.assertIn("ctf_explorer", card)
                self.assertNotIn("{username}", card)

    def test_remote_text_cannot_inject_svg(self):
        data = gen.summarize_player(self.player, self.scoreboard)
        data["username"] = '<script>alert("hello")</script> & a "quote"'
        for style in gen.TEMPLATE_COLOR_PALETTES:
            with self.subTest(style=style):
                card = gen.make_card(data, {}, style)
                root = ET.fromstring(card)
                self.assertEqual(root.findall(".//{http://www.w3.org/2000/svg}script"), [])
                self.assertIn(data["username"], "".join(root.itertext()))

    def test_same_stats_produce_identical_svg(self):
        data = gen.summarize_player(self.player, self.scoreboard)
        self.assertEqual(gen.make_card(data, {"Web": 4, "Crypto": 2}),
                         gen.make_card(data, {"Web": 4, "Crypto": 2}))

    def test_cli_works_from_another_directory_without_a_token(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            try:
                os.chdir(temp)
                with contextlib.redirect_stdout(io.StringIO()):
                    result = gen.main(["hacker", "--sample", "--output", "nested/card.svg",
                                       "--data-output", "nested/stats.json"])
                self.assertEqual(result, 0)
                ET.parse("nested/card.svg")
                self.assertEqual(json.loads(Path("nested/stats.json").read_text())["username"], "ctf_explorer")
                self.assertFalse(Path("player_data.json").exists())
            finally:
                os.chdir(previous)

    def test_invalid_style_fails_before_network_access(self):
        with patch.object(gen, "fetch_profile") as fetch, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                gen.main(["mistyped-theme"])
        self.assertEqual(caught.exception.code, 2)
        fetch.assert_not_called()

    def test_cli_missing_token_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            output = Path(temp) / "card.svg"
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(gen.main(["--output", str(output)]), 1)
            self.assertFalse(output.exists())


class RequestTests(unittest.TestCase):
    def test_request_has_timeout_and_status_check(self):
        session = Mock()
        session.get.return_value.json.return_value = {"score": 42}
        self.assertEqual(gen.fetch_json(session, gen.PLAYER_URL, {"Authorization": "Token sample"}), {"score": 42})
        self.assertEqual(session.get.call_args.kwargs["timeout"], (5, 30))
        session.get.return_value.raise_for_status.assert_called_once_with()

    def test_authentication_failure_has_a_safe_message(self):
        session = Mock()
        response = requests.Response()
        response.status_code = 401
        session.get.return_value.raise_for_status.side_effect = requests.HTTPError(
            "sensitive upstream body", response=response)
        with self.assertRaisesRegex(ValueError, "rejected OC_TOKEN") as caught:
            gen.fetch_json(session, gen.PLAYER_URL, {})
        self.assertNotIn("sensitive", str(caught.exception))

    def test_network_errors_and_bad_json_are_reported(self):
        session = Mock()
        session.get.side_effect = requests.Timeout("sensitive detail")
        with self.assertRaisesRegex(ValueError, "request timeout"):
            gen.fetch_json(session, gen.PLAYER_URL, {})
        session = Mock()
        session.get.return_value.json.side_effect = requests.exceptions.JSONDecodeError("bad", "", 0)
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            gen.fetch_json(session, gen.PLAYER_URL, {})


if __name__ == "__main__":
    unittest.main()
