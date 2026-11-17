"""Style sheets stay well-formed: an unbalanced brace makes browsers apply or drop every rule after it (in 4.40.0 a
moved block put the rest of theme.css inside a phone-only media query, so desktop lost e.g. the filter icon size)."""
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def problems(css):
    """Lines (1-based) where more braces close than opened, and the depth left at the end."""
    css = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group().count("\n"), css, flags=re.S)
    depth, negative = 0, []
    for number, line in enumerate(css.splitlines(), 1):
        depth += line.count("{") - line.count("}")
        if depth < 0:
            negative.append(number)
            depth = 0
    return negative, depth


class StaticCssTests(unittest.TestCase):
    def test_braces_balance_in_style_sheets_and_style_blocks(self):
        sources = {path.name: path.read_text() for path in STATIC.glob("*.css")}
        for page in STATIC.glob("*.html"):
            for i, block in enumerate(re.findall(r"<style>(.*?)</style>", page.read_text(), flags=re.S)):
                sources[f"{page.name} <style> {i + 1}"] = block
        self.assertIn("theme.css", sources)
        for name, css in sources.items():
            with self.subTest(name):
                self.assertEqual(problems(css), ([], 0))

    def test_detects_a_stray_brace(self):
        self.assertEqual(problems("a { color:red; }\n}\nb { }"), ([2], 0))
        self.assertEqual(problems("@media(max-width:600px) {\n a { }\n"), ([], 1))


if __name__ == "__main__":
    unittest.main()
