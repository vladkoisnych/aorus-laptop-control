"""GNOME Shell extension sanity, without needing GNOME.

The checks that need node or glib live in CI. These are the ones a plain Python
can do, and they catch the failure that is hardest to spot by eye: a settings
key the JS reads that the schema never declares, which throws at runtime inside
the shell where nobody sees it.
"""

import json
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from harness import REPO

EXT = REPO / "gnome-extension"


def has_extension():
    return (EXT / "metadata.json").exists()


@unittest.skipUnless(has_extension(), "no gnome-extension directory")
class TestMetadata(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.meta = json.loads((EXT / "metadata.json").read_text())

    def test_required_fields_are_present(self):
        for key in ("uuid", "name", "description", "shell-version"):
            self.assertIn(key, self.meta)

    def test_uuid_looks_like_a_uuid(self):
        self.assertRegex(self.meta["uuid"], r"^[\w.-]+@[\w.-]+$")

    def test_version_is_an_integer(self):
        # GNOME rejects a string here, and the error is opaque
        self.assertIsInstance(self.meta.get("version", 1), int)

    def test_shell_versions_are_strings_of_digits(self):
        for v in self.meta["shell-version"]:
            self.assertIsInstance(v, str)
            self.assertTrue(v.split(".")[0].isdigit(), v)

    def test_every_file_the_shell_loads_exists(self):
        for f in ("extension.js", "stylesheet.css"):
            self.assertTrue((EXT / f).exists(), f)
        if "settings-schema" in self.meta:
            self.assertTrue((EXT / "prefs.js").exists(),
                            "a settings schema without prefs.js is a dead Settings button")


@unittest.skipUnless(has_extension(), "no gnome-extension directory")
class TestSchema(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.meta = json.loads((EXT / "metadata.json").read_text())
        cls.files = sorted((EXT / "schemas").glob("*.gschema.xml"))
        cls.js = "\n".join((EXT / f).read_text() for f in ("extension.js", "prefs.js"))

    def test_exactly_one_schema_file(self):
        self.assertEqual(len(self.files), 1)

    def test_the_schema_id_matches_metadata(self):
        root = ET.parse(self.files[0]).getroot()
        schema = root.find("schema")
        self.assertEqual(schema.get("id"), self.meta["settings-schema"])

    def test_the_filename_matches_the_schema_id(self):
        root = ET.parse(self.files[0]).getroot()
        expected = root.find("schema").get("id") + ".gschema.xml"
        self.assertEqual(self.files[0].name, expected,
                         "glib-compile-schemas is fussy about this")

    def test_every_key_has_a_default_and_a_summary(self):
        root = ET.parse(self.files[0]).getroot()
        for key in root.find("schema").findall("key"):
            with self.subTest(key=key.get("name")):
                self.assertIsNotNone(key.find("default"))
                self.assertIsNotNone(key.find("summary"))

    def test_every_setting_the_code_reads_is_declared(self):
        root = ET.parse(self.files[0]).getroot()
        declared = {k.get("name") for k in root.find("schema").findall("key")}
        used = set(re.findall(r"get_(?:boolean|string|int)\('([\w-]+)'\)", self.js))
        used |= set(re.findall(r"set_(?:boolean|string|int)\('([\w-]+)'", self.js))
        used |= set(re.findall(r"settings\.bind\('([\w-]+)'", self.js))
        used |= set(re.findall(r"'changed::([\w-]+)'", self.js))
        undeclared = used - declared - {"color-scheme", "gtk-theme"}
        self.assertEqual(undeclared, set(),
                         f"read from GSettings but never declared: {undeclared}")

    def test_no_declared_key_is_unused(self):
        root = ET.parse(self.files[0]).getroot()
        declared = {k.get("name") for k in root.find("schema").findall("key")}
        unused = {k for k in declared if k not in self.js}
        self.assertEqual(unused, set(), f"declared but never read: {unused}")


@unittest.skipUnless(has_extension(), "no gnome-extension directory")
class TestStylesheet(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.css = (EXT / "stylesheet.css").read_text()
        cls.js = (EXT / "extension.js").read_text()

    def test_every_class_the_code_applies_is_styled(self):
        used = set()
        for tok in (set(re.findall(r"style_class: '([^']+)'", self.js))
                    | set(re.findall(r"style_class_name\('([^']+)'\)", self.js))):
            used |= set(tok.split())
        defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", self.css))
        missing = {c for c in used if c.startswith("aorusctl") and c not in defined}
        self.assertEqual(missing, set(), f"applied but never styled: {missing}")

    def test_braces_balance(self):
        self.assertEqual(self.css.count("{"), self.css.count("}"))


@unittest.skipUnless(has_extension(), "no gnome-extension directory")
class TestSources(unittest.TestCase):

    def test_the_entry_points_export_a_default_class(self):
        for f in ("extension.js", "prefs.js"):
            with self.subTest(file=f):
                self.assertRegex((EXT / f).read_text(),
                                 r"export default class",
                                 "GNOME 45+ loads extensions as ES modules")

    def test_extension_js_imports_from_the_shell_resource_paths(self):
        js = (EXT / "extension.js").read_text()
        self.assertIn("resource:///org/gnome/shell/ui/main.js", js)
        self.assertIn("resource:///org/gnome/shell/extensions/extension.js", js)

    def test_timers_and_signals_are_released_on_destroy(self):
        js = (EXT / "extension.js").read_text()
        destroy = js[js.index("destroy()"):]
        self.assertIn("GLib.source_remove", destroy,
                      "a leaked timeout keeps firing after the extension unloads")
        self.assertIn("disconnect", destroy, "a leaked signal handler leaks the object")

    def test_the_dashboard_address_defaults_to_loopback(self):
        root = ET.parse(sorted((EXT / "schemas").glob("*.xml"))[0]).getroot()
        for key in root.find("schema").findall("key"):
            if key.get("name") == "api-url":
                self.assertIn("127.0.0.1", key.find("default").text)


if __name__ == "__main__":
    unittest.main()
