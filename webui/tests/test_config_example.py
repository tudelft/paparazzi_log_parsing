import os
import unittest

import config


EXAMPLE = os.path.join(os.path.dirname(config.__file__), "logdb.env.example")


class ConfigExampleTest(unittest.TestCase):
    def test_example_file_lists_every_setting(self):
        with open(EXAMPLE, encoding="utf-8") as f:
            text = f.read()
        for items in config.SETTINGS.values():
            for name in items:
                self.assertRegex(text, rf"(?m)^#?LOGDB_{name}=", f"LOGDB_{name} missing from logdb.env.example")

    def test_example_file_matches_config(self):
        with open(EXAMPLE, encoding="utf-8") as f:
            self.assertEqual(f.read(), config.example() + "\n",
                             "logdb.env.example is out of date: cd webui && python3 config.py example > logdb.env.example")


if __name__ == "__main__":
    unittest.main()
