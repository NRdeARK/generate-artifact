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
                for number, label in enumerate(("Intent", "Use cases", "Decisions", "Concepts", "UC details", "Components")):
                    self.assertRegex(text, rf'<span class="tab-num">{number}</span><span class="tab-label"><span data-l="en">{label}</span>')
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
                self.assertRegex(text, r'class="tab disabled" aria-disabled="true" tabindex="0" title="Not created yet[^"]*"')
                self.assertEqual(text.count('class="tab disabled"'), 1)

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


class ReviewRegressionTests(FixtureCase):
    """One test per REVIEW_CODEX.md finding (F1-F8) and per extra spec gap (GA-GD)."""

    def render_text(self, tab):
        run("render", str(self.dir), "--tabs", tab)
        path = self.dir / "build" / f"{tab}.html"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_f1_markup_in_uc_id_is_escaped_and_rejected(self):
        payload = 'UC-01"><img src=x onerror=alert(1)>'
        self.edit("usecases.json", lambda d: d["ucs"][0].update(id=payload))
        self.assertNotIn("<img src=x onerror", self.render_text("tab4"))
        self.assertIn("bad-id", self.codes()[1])

    def test_f2_non_https_url_is_not_linked_and_is_an_error(self):
        self.edit("artifacts.json", lambda d: d["tabs"]["tab2"].update(url="javascript:alert(1)"))
        text = self.render_text("intent")
        self.assertNotIn("javascript:", text)
        self.assertRegex(text, r'class="tab disabled"[^>]*>(?:<span[^>]*>)*2<')
        code, codes, _ = self.codes()
        self.assertEqual(code, 1)
        self.assertIn("bad-url", codes)

    def test_f3_missing_id_duplicate_uc_and_empty_crud(self):
        self.edit("registry.json", lambda d: d["components"][0].pop("id"))
        self.assertIn("missing-id", self.codes()[1])
        self.setUp()
        self.edit("usecases.json", lambda d: d["ucs"].append(dict(d["ucs"][0])))
        self.assertIn("duplicate-id", self.codes()[1])
        self.setUp()
        self.edit("usecases.json", lambda d: d.update(crud=[]))
        self.assertIn("crud-missing", self.codes()[1])

    def test_f4_component_change_needs_decisions_and_files(self):
        def change(d):
            agg = next(c for c in d["components"] if c["id"] == "CMP-AGG")
            agg["change"].update(files=[], decisions=[])
        self.edit("registry.json", change)
        self.assertTrue({"change-no-decision", "change-no-files"} <= self.codes()[1])

    def test_f4_waiver_with_reason_is_accepted(self):
        def change(d):
            agg = next(c for c in d["components"] if c["id"] == "CMP-AGG")
            agg["change"].update(files=[], decisions=[], waive={"files": "not built yet", "decisions": "no fork"})
        self.edit("registry.json", change)
        self.assertEqual(self.codes()[0], 0)

    def test_f5_wrong_shapes_give_errors_not_tracebacks(self):
        self.edit("usecases.json", lambda d: d.update(ucs=None))
        self.assertIn(run("validate", str(self.dir))[0], (1, 2))
        self.setUp()
        (self.dir / "usecases.json").write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(run("validate", str(self.dir))[0], 2)
        self.setUp()
        self.edit("usecases.json", lambda d: d["ucs"][0].update(flows=None))
        self.assertIn(run("validate", str(self.dir))[0], (1, 2))
        self.setUp()
        self.edit("artifacts.json", lambda d: d.update(tabs=None))
        self.assertEqual(run("render", str(self.dir))[0], 0)

    def test_f6_python_39_is_documented_and_enforced(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("Python 3.9+", readme)
        self.assertNotIn("3.8", readme)
        self.assertIn("(3, 9)", (SKILL / "scripts" / "artifact_sync.py").read_text(encoding="utf-8"))

    def test_f7_every_details_block_is_open_in_print(self):
        project = sync.load_project(self.dir)
        document = sync.Site(project, print_mode=True).print_document(sync.present_tabs(self.dir))
        self.assertEqual(re.findall(r"<details(?![^>]*\bopen\b)[^>]*>", document), [])

    def test_f8_skill_commits_before_rendering(self):
        steps = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("## 5. Publish loop")[1].split("## 6.")[0]
        self.assertLess(steps.index("**Commit**"), steps.index("artifact_sync.py render"))

    def test_ga_affected_cause_must_be_a_changing_component(self):
        self.edit("usecases.json", lambda d: d["ucs"][2].update(caused_by="CMP-API"))
        self.assertIn("affected-bad-cause", self.codes()[1])

    def test_gb_validator_rejects_diagram_paths_the_renderer_rejects(self):
        (self.dir / "diagrams" / "notes.png").write_bytes(b"x")
        self.edit("concepts.json", lambda d: d["concepts"][0].update(diagram="diagrams/notes.png"))
        self.assertIn("bad-diagram-path", self.codes()[1])
        self.edit("concepts.json", lambda d: d["concepts"][0].update(diagram="../outside.svg"))
        (self.tmp / "outside.svg").write_text("<svg/>", encoding="utf-8")
        self.assertIn("bad-diagram-path", self.codes()[1])

    def test_gc_theme_flag_selects_theme_file(self):
        theme = self.tmp / "mine.css"
        theme.write_text(":root { --accent: #123456; }", encoding="utf-8")
        run("render", str(self.dir), "--theme", str(theme), "--tabs", "tab1")
        text = (self.dir / "build" / "tab1.html").read_text(encoding="utf-8")
        self.assertIn("--accent: #123456", text)
        self.assertNotIn("#D97757", text)
        self.assertEqual(run("render", str(self.dir), "--theme", "no-such-theme")[0], 2)
        self.assertEqual(run("export-pdf", str(self.dir), "--theme", "no-such-theme", "--dry-run")[0], 2)


class SecondReviewRegressionTests(FixtureCase):
    """One test per item in REVIEW_CODEX_2.md (N1-N3, F3 partial, F5 partial), using the report's payloads."""

    def set_url(self, url):
        self.edit("artifacts.json", lambda d: d["tabs"]["tab2"].update(url=url))

    def test_n1_backslash_authority_bypass_is_rejected(self):
        self.set_url("https://evil.example\\@claude.ai/a")
        code, codes, _ = self.codes()
        self.assertEqual(code, 1)
        self.assertIn("bad-url", codes)
        run("render", str(self.dir), "--tabs", "intent")
        self.assertNotIn("evil.example", (self.dir / "build" / "intent.html").read_text(encoding="utf-8"))

    def test_n1_other_url_forms_are_rejected(self):
        for url in ("https://user:pw@claude.ai/a", "https://claude.ai:8443/a", "https://claude.ai\t/a",
                    " https://claude.ai/a", "https://sub.claude.ai/a", "http://claude.ai/a", "https://CLAUDE.ai.evil.example/a"):
            with self.subTest(url=url):
                self.assertFalse(sync.safe_url(url))
        self.assertTrue(sync.safe_url("https://claude.ai/artifact/abc"))

    def test_n2_malformed_ipv6_url_reports_bad_url_instead_of_crashing(self):
        self.set_url("https://[invalid")
        code, codes, _ = self.codes()
        self.assertEqual(code, 1)
        self.assertIn("bad-url", codes)
        self.assertEqual(run("render", str(self.dir), "--tabs", "intent")[0], 0)

    def test_n3_waiver_reasons_must_be_nonblank_text(self):
        for bad in (True, ["x"], {"a": 1}, "   ", 1):
            with self.subTest(waiver=bad):
                self.setUp()

                def change(d, bad=bad):
                    agg = next(c for c in d["components"] if c["id"] == "CMP-AGG")
                    agg["change"].update(files=[], decisions=[], waive={"files": bad, "decisions": bad})
                self.edit("registry.json", change)
                self.assertTrue({"change-no-files", "change-no-decision"} <= self.codes()[1])

    def test_n3_crud_waiver_must_be_nonblank_text(self):
        for bad in (True, "  ", ["x"]):
            with self.subTest(waiver=bad):
                self.setUp()
                self.edit("usecases.json", lambda d, bad=bad: d.update(crud=[], crud_waiver=bad))
                self.assertIn("crud-missing", self.codes()[1])
        self.setUp()
        self.edit("usecases.json", lambda d: d.update(crud=[], crud_waiver="nothing is stored"))
        self.assertEqual(self.codes()[0], 0)

    def test_f3_missing_crud_row_for_a_stored_entity_is_an_error(self):
        self.edit("usecases.json", lambda d: d.update(crud=d["crud"][:2]))  # drops the Weekly report row
        code, codes, out = self.codes()
        self.assertEqual(code, 1)
        self.assertIn("crud-missing-entity", codes)
        self.assertIn("Weekly report", out)

    def test_f3_stored_component_needs_a_row(self):
        self.edit("usecases.json", lambda d: d.update(crud=[r for r in d["crud"] if r["entity"] != "Projects DB"]))
        out = self.codes()[2]
        self.assertIn("crud-missing-entity", out)
        self.assertIn("Projects DB", out)

    def test_f5_non_string_id_does_not_crash_renderer_construction(self):
        self.edit("usecases.json", lambda d: d["ucs"][0].update(id=[]))
        self.assertIn("bad-id", self.codes()[1])
        self.assertIn(run("render", str(self.dir))[0], (0, 1))
        self.assertIn(run("export-pdf", str(self.dir), "--dry-run")[0], (0, 1))
        self.assertIn(run("i18n-diff", str(self.dir))[0], (0, 1))


class DiagramTests(unittest.TestCase):
    def test_fixture_diagrams_are_real_self_contained_svgs(self):
        svgs = sorted((FIXTURE / "diagrams").glob("*.svg"))
        self.assertEqual(len(svgs), 23)
        for path in svgs:
            text = path.read_text(encoding="utf-8")
            with self.subTest(svg=path.name):
                self.assertNotIn("placeholder diagram", text)
                self.assertNotIn("<script", text)
                self.assertNotRegex(text, r"(?:href|src)=")
                self.assertNotIn("http://", text.replace("http://www.w3.org/2000/svg", ""))
                self.assertIn('role="img"', text)
                self.assertRegex(text, r"<title id=[^>]+>[^<]+</title>")

    def test_sequence_diagrams_label_every_arrow(self):
        for path in [*(FIXTURE / "diagrams").glob("uc-0*.svg"), *(FIXTURE / "diagrams").glob("arch-*.svg")]:
            text = path.read_text(encoding="utf-8")
            arrows = len(re.findall(r'marker-end="url\(#[^)]*arrow', text))
            labels = len(re.findall(r'letter-spacing="0.06em"', text))
            with self.subTest(svg=path.name):
                self.assertGreaterEqual(labels, arrows)

    def test_skill_requires_diagram_design_and_forbids_placeholders(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("diagram-design:diagram-design", text)
        self.assertIn("docs/artifact/diagrams/<name>.svg", text)
        self.assertIn("Never hand-draw placeholder", text)


class ArchViewTests(FixtureCase):
    """Before/after data-flow diagrams per C4 level (Tab 5 and the Tab 1 overview)."""

    def page(self, tab):
        run("render", str(self.dir), "--tabs", tab)
        return (self.dir / "build" / f"{tab}.html").read_text(encoding="utf-8")

    def test_each_level_with_components_gets_a_diagram_pair_and_a_switch(self):
        for tab in ("tab5", "tab1"):
            text = self.page(tab)
            with self.subTest(tab=tab):
                self.assertEqual(re.findall(r'class="ba-cols" data-level="(L\d)"', text), ["L1", "L2", "L3"])
                for level in ("L1", "L2", "L3"):
                    self.assertIn(f'data-level-btn="{level}"', text)
                self.assertRegex(text, r'class="ba-cols" data-level="L1" hidden')
                self.assertNotRegex(text, r'class="ba-cols" data-level="L2" hidden')
                self.assertEqual(text.count("data:image/svg+xml;base64,"), 6 + (tab == "tab1"))
                self.assertIn("1 new", text)  # change summary line kept

    def test_tab5_keeps_component_badges(self):
        self.assertIn('class="badge new"', self.page("tab5"))

    def test_missing_view_file_is_a_placeholder_and_a_warning(self):
        (self.dir / "diagrams" / "arch-L2-after.svg").unlink()
        self.assertIn("diagrams/arch-L2-after.svg", self.page("tab5"))
        self.assertIn("diagram-missing", self.codes()[1])

    def test_level_with_components_but_no_view_is_a_warning(self):
        self.edit("registry.json", lambda d: d["views"].pop("L3"))
        code, codes, _ = self.codes()
        self.assertIn("view-missing", codes)
        self.assertEqual(code, 0)

    def test_level_without_components_needs_no_view(self):
        def change(d):
            d["components"] = [c for c in d["components"] if c["c4_level"] != "L3"]
            d["views"].pop("L3")
        self.edit("registry.json", change)
        self.assertNotIn("view-missing", self.codes()[1])

    def test_view_paths_follow_the_diagram_path_rules(self):
        self.edit("registry.json", lambda d: d["views"]["L2"].update(before="diagrams/arch.png"))
        self.assertIn("bad-diagram-path", self.codes()[1])

    def test_print_shows_every_level(self):
        project = sync.load_project(self.dir)
        document = sync.Site(project, print_mode=True).print_document(["tab5"])
        self.assertEqual(len(re.findall(r'class="ba-cols" data-level="L\d"\s*>', document)), 3)


class Tab1LayoutTests(FixtureCase):
    def test_id_references_never_wrap_and_crud_table_has_its_own_row(self):
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.ref[^{]*\{[^}]*white-space:\s*nowrap")
        template = (SKILL / "templates" / "tab1.html").read_text(encoding="utf-8")
        crud_at, impact_at = template.index("t1.crud"), template.index("t1.impact")
        between = template[impact_at:crud_at]
        self.assertIn("</section>", between)  # impact and CRUD are separate full-width cards
        self.assertNotIn('class="grid2"', template[impact_at - 40:crud_at + 40])


class Tab1OutcomeTests(FixtureCase):
    def page(self):
        run("render", str(self.dir), "--tabs", "tab1")
        return (self.dir / "build" / "tab1.html").read_text(encoding="utf-8")

    def test_outcome_banner_tree_diagram_and_uc_table(self):
        text = self.page()
        self.assertRegex(text, r'<div class="banner">')
        self.assertEqual(text.count("data:image/svg+xml;base64,"), 7)  # 6 architecture views + the use case tree
        self.assertNotIn("impact sweep)", text)  # the separate affected-UC list is gone
        table = text.split('<table class="uc-table">')[1].split("</table>")[0]
        for header in ("ID", "Tier", "Plain-words summary", "Traced from", "Details"):
            self.assertIn(header, table)
        self.assertEqual(table.count("<tr>") - 1, 3)  # one row per UC
        self.assertIn("Outcome", table)
        self.assertIn("via", table)
        row = next(r for r in table.split("<tr>") if ">UC-03<" in r)
        self.assertIn("Projects DB", row)
        self.assertIn("PLACEHOLDER-tab4#UC-03", table)

    def test_main_and_affected_rows_are_styled_differently(self):
        table = self.page().split('<table class="uc-table">')[1]
        self.assertIn('class="pill main"', table)
        self.assertIn('class="pill affected"', table)

    def test_missing_tree_diagram_is_a_placeholder_and_a_warning(self):
        (self.dir / "diagrams" / "uc-tree.svg").unlink()
        self.assertIn("diagrams/uc-tree.svg", self.page())
        self.assertIn("diagram-missing", self.codes()[1])


class TabBarTests(FixtureCase):
    def page(self, tab="tab2"):
        run("render", str(self.dir), "--tabs", tab)
        return (self.dir / "build" / f"{tab}.html").read_text(encoding="utf-8")

    def test_numbered_tabs_current_marked_and_grouped(self):
        text = self.page("tab2")
        nav = text.split('<nav class="nav"')[1].split("</nav>")[0]
        self.assertEqual(re.findall(r'<span class="tab-num">(\d)</span>', nav), list("012345"))
        self.assertEqual(nav.count('aria-current="page"'), 1)
        self.assertRegex(nav, r'class="tab current" aria-current="page"><span class="tab-num">2</span>')
        self.assertIn('class="nav-divider"', nav)
        intent_group, solution_group = nav.split('class="nav-divider"')
        self.assertIn('<span class="tab-num">0</span>', intent_group)
        self.assertNotIn('<span class="tab-num">0</span>', solution_group)
        self.assertEqual(len(re.findall(r'class="tab-num"', solution_group)), 5)

    def test_disabled_tab_has_lock_and_tooltip_not_strikethrough(self):
        self.edit("artifacts.json", lambda d: d["tabs"].pop("tab5"))
        text = self.page("tab2")
        disabled = re.search(r'<span class="tab disabled".*?</svg></span>', text, re.S).group(0)
        self.assertIn("tab-lock", disabled)
        self.assertIn('title="Not created yet', disabled)
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"\.tab[^{]*\{[^}]*line-through")

    def test_header_is_sticky_and_tab_states_use_theme_tokens(self):
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.top \{[^}]*position: sticky")
        for token in ("--tab-bg", "--tab-hover-bg", "--tab-active-bg", "--tab-active-fg", "--tab-disabled-fg", "--focus-ring"):
            self.assertIn(token, css)
            self.assertIn(token + ":", (SKILL / "themes" / "anthropic.css").read_text(encoding="utf-8"))
        self.assertIn("a.tab:hover", css)
        self.assertIn(".tab:focus-visible", css)


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
