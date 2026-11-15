import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "generate-artifact"
FIXTURE = SKILL / "examples" / "sample-project" / "docs" / "artifact"
sys.path.insert(0, str(SKILL / "scripts"))

import artifact_sync as sync  # noqa: E402


def run(*argv):
    """Run the CLI quietly; return (exit_code, stdout)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = sync.main(list(argv))
    return code, out.getvalue()


class FixtureCase(unittest.TestCase):
    """Each test gets its own copy of the fixture so seeded errors never leak."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.dir = self.tmp / "artifact"
        shutil.copytree(FIXTURE, self.dir, ignore=shutil.ignore_patterns("build"))

    def edit(self, filename, change):
        path = self.dir / filename
        data = json.loads(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def codes(self):
        code, out = run("validate", str(self.dir))
        return code, set(re.findall(r"\[(?:ERROR|WARNING)\] (\S+)", out)), out


class ValidateTests(FixtureCase):
    def test_fixture_passes(self):
        code, codes, out = self.codes()
        self.assertEqual(code, 0, out)
        self.assertEqual(codes, set())

    def test_missing_registry_id(self):
        self.edit("usecases.json", lambda d: d["ucs"][0]["components"].append("CMP-NOPE"))
        code, codes, out = self.codes()
        self.assertEqual(code, 1)
        self.assertIn("unknown-id", codes)
        self.assertIn("CMP-NOPE", out)

    def test_orphan_uc(self):
        self.edit("usecases.json", lambda d: d["ucs"][1].update(components=[]))
        code, codes, _ = self.codes()
        self.assertEqual(code, 1)
        self.assertIn("orphan-uc", codes)

    def test_crud_gap(self):
        self.edit("usecases.json", lambda d: d["crud"][0].update(delete=""))
        code, codes, _ = self.codes()
        self.assertEqual(code, 1)
        self.assertIn("crud-gap", codes)

    def test_stale_thai_is_flagged_and_strict_fails(self):
        self.edit("usecases.json", lambda d: d["ucs"][0].update(summary="Changed English text."))
        code, codes, _ = self.codes()
        self.assertIn("th-stale", codes)
        self.assertEqual(code, 0, "stale Thai is a warning by default")
        self.assertEqual(run("validate", str(self.dir), "--strict")[0], 1)

    def test_missing_thai_entry(self):
        self.edit("i18n/th.json", lambda d: d.pop("UC-02"))
        self.assertIn("th-missing", self.codes()[1])

    def test_component_change_without_reason_or_uc(self):
        self.edit("registry.json", lambda d: d["components"][-1]["change"].update(why="", ucs=[]))
        _, codes, _ = self.codes()
        self.assertTrue({"change-no-reason", "change-no-uc"} <= codes)

    def test_decision_without_uc_and_deferred_without_revisit(self):
        def change(d):
            d["decisions"][0]["ucs"] = []
            d["decisions"][1]["options"][1].pop("revisit_when")
        self.edit("decisions.json", change)
        _, codes, _ = self.codes()
        self.assertTrue({"decision-no-uc", "deferred-no-revisit"} <= codes)

    def test_uc_without_good_flow_and_concept_without_diagram(self):
        self.edit("usecases.json", lambda d: d["ucs"][2].update(flows=[]))
        self.edit("concepts.json", lambda d: d["concepts"][0].pop("diagram"))
        _, codes, _ = self.codes()
        self.assertTrue({"no-flow", "concept-no-diagram"} <= codes)

    def test_bad_json_reports_file_and_line(self):
        (self.dir / "registry.json").write_text("{ nope", encoding="utf-8")
        code, _ = run("validate", str(self.dir))
        self.assertEqual(code, 2)


class RenderTests(FixtureCase):
    def build(self, *extra):
        code, _ = run("render", str(self.dir), *extra)
        self.assertEqual(code, 0)
        return {p.name: p.read_text(encoding="utf-8") for p in (self.dir / "build").glob("*.html")}

    def test_six_pages_with_nav_toggle_and_theme(self):
        pages = self.build()
        self.assertEqual(sorted(pages), ["intent.html"] + [f"tab{n}.html" for n in range(1, 6)])
        for name, text in pages.items():
            with self.subTest(page=name):
                self.assertIn("data-set-lang=\"th\"", text)
                self.assertIn("ga-lang", text)
                self.assertIn("--accent: #D97757", text)
                for label in ("0 Intent", "1 Use cases", "2 Decisions", "3 Concepts", "4 UC details", "5 Components"):
                    self.assertIn(label, text)
                self.assertIn("updated", text)
                self.assertIn('data-l="th"', text)

    def test_titles_follow_naming_convention(self):
        pages = self.build()
        self.assertIn("<title>Status Board · 2 Decisions</title>", pages["tab2.html"])
        self.assertIn("<title>Status Board · 0 Intent</title>", pages["intent.html"])

    def test_tab_without_artifact_url_is_disabled_not_hidden(self):
        self.edit("artifacts.json", lambda d: d["tabs"].pop("tab5"))
        pages = self.build()
        for name, text in pages.items():
            if name == "tab5.html":
                continue  # the page itself is shown as current, not disabled
            with self.subTest(page=name):
                self.assertRegex(text, r'class="nav-disabled" aria-disabled="true" title="Not created yet[^"]*"')
                self.assertEqual(text.count("nav-disabled\""), 1)

    def test_tab4_alts_have_working_toggles_and_start_hidden(self):
        text = self.build()["tab4.html"]
        self.assertIn('data-toggle="UC-01-bad"', text)
        self.assertRegex(text, r'class="alt bad" data-group="UC-01-bad" hidden')
        self.assertIn('aria-expanded="false"', text)

    def test_tab2_has_no_choose_buttons_and_decided_is_collapsed(self):
        text = self.build()["tab2.html"]
        self.assertNotIn("<button", text.split("</header>")[1])
        self.assertRegex(text, r'<details class="card decision " id="D1" >')
        self.assertRegex(text, r'<details class="card decision open-state" id="D3" open>')

    def test_missing_svg_becomes_placeholder_naming_the_file(self):
        (self.dir / "diagrams" / "concept-c1.svg").unlink()
        self.assertIn("diagrams/concept-c1.svg", self.build()["tab3.html"])
        self.assertIn("Diagram not found", self.build()["tab3.html"])

    def test_diagram_path_outside_project_is_not_embedded(self):
        self.edit("concepts.json", lambda d: d["concepts"][0].update(diagram="../../../../secret.svg"))
        self.assertIn("Diagram not found", self.build()["tab3.html"])

    def test_tabs_option_and_absent_data_are_skipped(self):
        (self.dir / "concepts.json").unlink()
        run("render", str(self.dir))
        built = sorted(p.name for p in (self.dir / "build").glob("*.html"))
        self.assertNotIn("tab3.html", built)
        self.assertEqual(len(built), 5)

    def test_html_escapes_content(self):
        self.edit("concepts.json", lambda d: d["concepts"][0].update(title="<script>alert(1)</script>"))
        text = self.build()["tab3.html"]
        self.assertNotIn("<script>alert(1)</script>", text)
        self.assertIn("&lt;script&gt;", text)

    def test_dry_run_writes_nothing(self):
        code, _ = run("render", str(self.dir), "--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse((self.dir / "build").exists())


class ExportTests(FixtureCase):
    def test_dry_run_writes_nothing(self):
        self.assertEqual(run("export-pdf", str(self.dir), "--dry-run")[0], 0)
        self.assertFalse((self.dir / "build").exists())

    def test_print_html_expands_everything(self):
        project = sync.load_project(self.dir)
        document = sync.Site(project, print_mode=True).print_document(sync.present_tabs(self.dir))
        self.assertIn("print-cover", document)
        self.assertIn("Status Board", document)
        self.assertNotRegex(document, r'class="alt \w+" data-group="[^"]+" hidden')
        self.assertRegex(document, r'<details class="card decision " id="D1" open>')
        self.assertNotIn('class="lang"', document)


class I18nTests(FixtureCase):
    def test_i18n_diff_lists_only_missing_and_stale(self):
        self.edit("usecases.json", lambda d: d["ucs"][0].update(title="Renamed"))
        self.edit("i18n/th.json", lambda d: d.pop("C1"))
        code, out = run("i18n-diff", str(self.dir))
        entries = {e["key"]: e["status"] for e in json.loads(out)}
        self.assertEqual(code, 0)
        self.assertEqual(entries, {"UC-01": "stale", "C1": "missing"})

    def test_ui_catalogs_have_same_keys(self):
        en = json.loads((SKILL / "i18n" / "en.json").read_text(encoding="utf-8"))
        th = json.loads((SKILL / "i18n" / "th.json").read_text(encoding="utf-8"))
        self.assertEqual(set(en), set(th))


class TemplateHygieneTests(unittest.TestCase):
    def test_templates_have_no_hex_colors_or_font_names(self):
        offenders = []
        for path in (SKILL / "templates").iterdir():
            text = path.read_text(encoding="utf-8")
            for pattern in (r"#[0-9a-fA-F]{3,8}\b", r"\brgba?\(", r"(?i)font-family:(?!\s*var\()", r"(?i)\b(inter|georgia|arial|helvetica|segoe|serif)\b"):
                offenders += [f"{path.name}: {m.group(0)}" for m in re.finditer(pattern, text)]
        self.assertEqual(offenders, [])

    def test_manifests_are_valid_json(self):
        for name in ("plugin.json", "marketplace.json"):
            data = json.loads((REPO / ".claude-plugin" / name).read_text(encoding="utf-8"))
            self.assertEqual(data["name"], "generate-artifact")
        self.assertEqual(json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))["plugins"][0]["source"], "./")


if __name__ == "__main__":
    unittest.main()
