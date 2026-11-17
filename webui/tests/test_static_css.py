"""Static pages stay well-formed. Style sheets: an unbalanced brace makes browsers apply or drop every rule after it (in 4.40.0 a
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


def duplicate_functions(js):
    """Top-level function names declared more than once: in a script the last one silently wins (a second
    loadFiles in logbook.html once replaced the first and broke the page)."""
    names = re.findall(r"^(?:async )?function (\w+)\s*\(", js, flags=re.M)
    return sorted({name for name in names if names.count(name) > 1})


class StaticScriptTests(unittest.TestCase):
    def test_no_function_is_declared_twice_in_a_page_or_script(self):
        sources = {path.name: path.read_text() for path in STATIC.glob("*.js")}
        for page in STATIC.glob("*.html"):
            sources[page.name] = "\n".join(re.findall(r"<script>(.*?)</script>", page.read_text(), flags=re.S))
        for name, js in sources.items():
            with self.subTest(name):
                self.assertEqual(duplicate_functions(js), [])

    def test_detects_a_duplicate(self):
        self.assertEqual(duplicate_functions("function a() {}\nasync function a() {}\nfunction b() {}"), ["a"])


if __name__ == "__main__":
    unittest.main()
