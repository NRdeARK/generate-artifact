import contextlib
import io
import json
import os
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


def run_probe(page_html, probe_js, target, size="1200,900"):
    """Open a rendered page in headless Edge/Chrome, run probe_js after load, return the string the probe wrote into <pre id=res>."""
    import subprocess
    script = ("<script>window.addEventListener('load',function(){var out=[];try{" + probe_js +
              "}catch(e){out.push('ERROR '+e.message)};document.body.insertAdjacentHTML('afterbegin','<pre id=res>'+out.join(';')+'</pre>');});</script></body>")
    target.write_text(page_html.replace("</body>", script), encoding="utf-8")
    done = subprocess.run([sync.find_browser(), "--headless", "--disable-gpu", f"--window-size={size}", "--virtual-time-budget=4000", "--dump-dom", target.resolve().as_uri()],
                          capture_output=True, text=True, encoding="utf-8", timeout=90)
    found = re.search(r'<pre id="res">([^<]*)</pre>', done.stdout)
    if not found:
        raise AssertionError("probe produced no result: " + done.stderr[-300:])
    return found.group(1)


def luminance(rgb):
    channels = [c / 255 for c in rgb]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)

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

    def test_tab4_fragment_toggles_start_off_and_target_the_inline_diagram(self):
        text = self.build()["tab4.html"]
        self.assertIn('data-target="uc-01" data-kind="bad" aria-pressed="false"', text)
        self.assertRegex(text, r'<g data-block="1" data-y="\d+" data-h="\d+" data-fragment="bad-timeout" data-kind="bad" display="none">')

    def test_tab2_has_no_choose_buttons_and_decided_is_collapsed(self):
        text = self.build()["tab2.html"]
        content = text.split("<main>")[1].split("</main>")[0]
        self.assertEqual(re.findall(r'<button(?![^>]*class="expand")', content), [])  # only diagram expand buttons
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
        self.assertNotIn("--accent: #D97757", text)
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
        self.assertEqual(len(svgs), 33)
        for path in svgs:
            text = path.read_text(encoding="utf-8")
            with self.subTest(svg=path.name):
                self.assertNotIn("placeholder diagram", text)
                self.assertEqual(sync.sanitize_svg(text)[1], [])  # no script, handler or external reference
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

    def test_each_overview_level_gets_a_diagram_pair_and_a_switch(self):
        for tab, figures in (("tab5", 21), ("tab1", 5)):
            text = self.page(tab)
            with self.subTest(tab=tab):
                self.assertEqual(re.findall(r'class="ba-cols" data-level="(L\d)"', text), ["L1", "L2"])
                for level in ("L1", "L2"):
                    self.assertIn(f'data-level-btn="{level}"', text)
                self.assertNotIn('data-level-btn="L3"', text)
                self.assertRegex(text, r'class="ba-cols" data-level="L1" hidden')
                self.assertNotRegex(text, r'class="ba-cols" data-level="L2" hidden')
                self.assertEqual(text.count('<figure class="diagram">'), figures)
                self.assertIn("1 new", text)  # change summary line kept

    def test_tab5_keeps_component_badges(self):
        self.assertIn('class="badge new"', self.page("tab5"))

    def test_missing_view_file_is_a_placeholder_and_a_warning(self):
        (self.dir / "diagrams" / "arch-L2-after.svg").unlink()
        self.assertIn("diagrams/arch-L2-after.svg", self.page("tab5"))
        self.assertIn("diagram-missing", self.codes()[1])

    def test_level_with_components_but_no_view_is_a_warning(self):
        self.edit("registry.json", lambda d: d["views"].pop("L2"))
        code, codes, _ = self.codes()
        self.assertIn("view-missing", codes)
        self.assertEqual(code, 0)

    def test_level_without_components_needs_no_view(self):
        def change(d):
            d["components"] = [c for c in d["components"] if c["c4_level"] != "L1"]
            d["views"].pop("L1")
        self.edit("registry.json", change)
        self.assertNotIn("view-missing", self.codes()[1])

    def test_view_paths_follow_the_diagram_path_rules(self):
        self.edit("registry.json", lambda d: d["views"]["L2"].update(before="diagrams/arch.png"))
        self.assertIn("bad-diagram-path", self.codes()[1])

    def test_print_shows_every_level(self):
        project = sync.load_project(self.dir)
        document = sync.Site(project, print_mode=True).print_document(["tab5"])
        self.assertEqual(len(re.findall(r'class="ba-cols" data-level="L\d"\s*>', document)), 2)


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
        self.assertEqual(text.count('<figure class="diagram">'), 5)  # four overview diagrams (L1, L2 pairs) + the use case tree
        self.assertNotIn("impact sweep)", text)  # the separate affected-UC list is gone
        table = text.split('<table class="uc-table stack">')[1].split("</table>")[0]
        for header in ("ID", "Tier", "Plain-words summary", "Traced from", "Details"):
            self.assertIn(header, table)
        self.assertEqual(table.count("<tr>"), 3)  # one data row per UC (the header row is <tr class="stack-head">)
        self.assertIn("Outcome", table)
        self.assertIn("via", table)
        row = next(r for r in table.split("<tr>") if ">UC-03<" in r)
        self.assertIn("Projects DB", row)
        self.assertIn("PLACEHOLDER-tab4#UC-03", table)

    def test_main_and_affected_rows_are_styled_differently(self):
        table = self.page().split('<table class="uc-table stack">')[1]
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


class DiagramViewerTests(FixtureCase):
    def pages(self):
        run("render", str(self.dir))
        return {p.stem: p.read_text(encoding="utf-8") for p in (self.dir / "build").glob("*.html")}

    def test_every_diagram_has_a_real_expand_button(self):
        for name, text in self.pages().items():
            figures = text.count('<figure class="diagram">')
            buttons = re.findall(r'<button type="button" class="expand" aria-label="Expand diagram[^"]*">', text)
            with self.subTest(page=name):
                self.assertEqual(len(buttons), figures)
                self.assertIn("cursor: zoom-in", (SKILL / "templates" / "base.css").read_text(encoding="utf-8"))
        self.assertGreater(sum(t.count('class="expand"') for t in self.pages().values()), 20)

    def test_diagrams_in_collapsed_details_and_hidden_fragments_are_covered(self):
        tab4 = self.pages()["tab4"]
        detail = re.search(r"<details class=\"card\" ><summary>[^<]*<span[^>]*>Detail.*?</details>", tab4, re.S)
        self.assertIsNotNone(detail)
        self.assertIn('class="expand"', detail.group(0))
        uc1 = tab4.split('<section class="uc" id="UC-01">')[1].split("</section>")[0]
        self.assertIn('display="none"', uc1)  # hidden fragments live inside the one inline diagram, which has its own button
        self.assertEqual(uc1.count('class="expand"'), 1)

    def test_overlay_markup_is_in_every_page_with_all_controls(self):
        for name, text in self.pages().items():
            with self.subTest(page=name):
                self.assertRegex(text, r'<div class="viewer" id="viewer" role="dialog" aria-modal="true"[^>]*hidden>')
                for control in ("in", "out", "reset", "close"):
                    self.assertIn(f'data-viewer="{control}"', text)

    def test_viewer_script_handles_keyboard_wheel_pointer_focus_and_avoids_popups(self):
        js = (SKILL / "templates" / "base.js").read_text(encoding="utf-8")
        for needle in ("Escape", "wheel", "pointerdown", "pointermove", "lastTrigger.focus()", "viewer-stage", "data-compare", "syncZoom", "syncPan"):
            self.assertIn(needle, js)
        self.assertNotIn("window.open", js)
        self.assertNotRegex(js, r"(?<!try \{ )requestFullscreen\(")

    def test_print_css_hides_the_viewer_and_print_export_has_no_buttons(self):
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        print_block = css.split("@media print")[1].split("@media (max-width")[0]
        self.assertIn(".expand", print_block)
        self.assertIn(".viewer", print_block)
        project = sync.load_project(self.dir)
        document = sync.Site(project, print_mode=True).print_document(sync.present_tabs(self.dir))
        self.assertNotIn('class="expand"', document)
        self.assertNotIn('id="viewer"', document)

    def test_viewer_uses_theme_tokens_only(self):
        theme = (SKILL / "themes" / "anthropic.css").read_text(encoding="utf-8")
        for token in ("--viewer-bg", "--viewer-fg", "--viewer-btn-bg"):
            self.assertIn(token + ":", theme)

    @unittest.skipUnless(sync.find_browser(), "needs headless Edge or Chrome")
    def test_in_browser_open_zoom_pan_close_and_focus_return(self):
        probe = """var btn=document.querySelector('.expand'),v=document.getElementById('viewer'),stage=document.getElementById('viewer-stage'),img=v.querySelector('.viewer-img');
btn.focus(); btn.click();
out.push('open='+(!v.hidden)); out.push('inline='+(img.querySelector('svg')!==null));
var t0=img.style.transform;
document.querySelector('[data-viewer=in]').click(); var t1=img.style.transform; out.push('zoomed='+(t1!==t0));
stage.dispatchEvent(new WheelEvent('wheel',{deltaY:-200,clientX:300,clientY:300,bubbles:true,cancelable:true})); out.push('wheel='+(img.style.transform!==t1));
document.querySelector('[data-viewer=reset]').click(); out.push('reset='+(img.style.transform===t0));
document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
out.push('closed='+v.hidden); out.push('focus='+(document.activeElement===btn));
btn.click(); stage.dispatchEvent(new MouseEvent('click',{bubbles:true})); out.push('backdrop='+v.hidden)"""
        result = run_probe(self.pages()["tab3"], probe, self.dir / "build" / "probe.html")
        self.assertEqual(result, "open=true;inline=true;zoomed=true;wheel=true;reset=true;closed=true;focus=true;backdrop=true")


class ConceptIndexTests(FixtureCase):
    def page(self):
        run("render", str(self.dir), "--tabs", "tab3")
        return (self.dir / "build" / "tab3.html").read_text(encoding="utf-8")

    def test_chips_replaced_by_a_side_index_with_id_title_and_kind(self):
        text = self.page()
        self.assertNotIn('class="chips"', text)
        self.assertNotIn('class="chip"', text)
        side = text.split('<aside class="side"')[1].split("</aside>")[0]
        for ident, title, plain in (("C1", "Event-driven status", "everyone sees it within seconds"), ("C2", "Aggregator", "behind-the-scenes helper"),
                                    ("C3", "Status history", "we keep every change")):
            self.assertIn(f'href="#{ident}"', side)
            self.assertIn(title, side)
            self.assertIn(plain, side)
        self.assertEqual(re.findall(r'href="#(C\d)"', side), ["C1", "C2", "C3"])

    def test_concept_blocks_live_in_the_main_column(self):
        text = self.page()
        self.assertRegex(text, r'<div class="split">\s*<aside class="side"')
        main_col = text.split('<div class="main-col">')[1]
        self.assertEqual(re.findall(r'<article class="card blk" id="(C\d)"', main_col), ["C1", "C2", "C3"])

    def test_scrollspy_script_uses_intersection_observer_with_a_scroll_fallback(self):
        js = (SKILL / "templates" / "base.js").read_text(encoding="utf-8")
        self.assertIn("IntersectionObserver", js)
        self.assertIn('"scroll"', js)
        self.assertIn("aria-current", js)
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        self.assertIn(".side a.active", css)
        self.assertIn("scroll-margin-top", css)

    def test_tab4_sidebar_gets_the_same_highlight_and_stacks_on_phones(self):
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        phone = css.split("@media (max-width: 760px)")[1]
        self.assertIn(".split { flex-direction: column; }", phone)
        self.assertIn("position: static", phone)

    @unittest.skipUnless(sync.find_browser(), "needs headless Edge or Chrome")
    def test_in_browser_scrollspy_marks_the_concept_in_view(self):
        page = self.page()
        probe = """<script>window.addEventListener('load',function(){
var first=document.querySelector('.side a.active'); var out=['first='+(first?first.getAttribute('href'):'none')];
window.scrollTo(0,1000000);
var tries=0;
(function wait(){var a=document.querySelector('.side a.active');
 if((a&&a.getAttribute('href')==='#C3')||++tries>40){
  out.push('last='+(a?a.getAttribute('href'):'none')); out.push('current='+(a&&a.getAttribute('aria-current')));
  document.body.insertAdjacentHTML('afterbegin','<pre id=res>'+out.join(';')+'</pre>');
 } else { setTimeout(wait,100); }})();
});</script></body>"""
        target = self.dir / "build" / "probe3.html"
        target.write_text(page.replace("</body>", probe), encoding="utf-8")
        import subprocess
        done = subprocess.run([sync.find_browser(), "--headless", "--disable-gpu", "--window-size=1200,420", "--virtual-time-budget=4000", "--dump-dom", target.resolve().as_uri()],
                              capture_output=True, text=True, encoding="utf-8", timeout=90)
        result = re.search(r'<pre id="res">([^<]*)</pre>', done.stdout)
        self.assertIsNotNone(result, done.stderr[-300:])
        self.assertEqual(result.group(1), "first=#C1;last=#C3;current=true")


class SvgSanitizerTests(unittest.TestCase):
    def clean(self, body, root_attrs=""):
        return sync.sanitize_svg(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"{root_attrs}>{body}</svg>')

    def test_script_foreignobject_and_style_elements_are_removed(self):
        clean, problems = self.clean('<script>alert(1)</script><foreignObject><div>x</div></foreignObject><style>*{}</style><rect width="1" height="1"/>')
        self.assertNotIn("script", clean)
        self.assertNotIn("foreignObject", clean)
        self.assertNotIn("<style", clean)
        self.assertIn("<rect", clean)
        self.assertEqual(len(problems), 3)

    def test_event_handler_and_javascript_attributes_are_removed(self):
        clean, problems = self.clean('<rect onclick="alert(1)" onload="x()" width="1" height="1" fill="javascript:alert(1)"/>', ' onload="alert(2)"')
        self.assertNotRegex(clean, r"on(click|load)=")
        self.assertNotIn("javascript", clean)
        self.assertGreaterEqual(len(problems), 4)

    def test_external_references_are_removed_but_local_ones_stay(self):
        clean, problems = self.clean('<use href="https://evil.example/x.svg#a"/><rect fill="url(https://evil.example/p)" width="1" height="1"/>'
                                     '<path d="M0 0" marker-end="url(#m)"/><marker id="m"/>')
        self.assertNotIn("evil.example", clean)
        self.assertIn("url(#m)", clean)
        self.assertTrue(problems)

    def test_doctype_entities_and_non_svg_roots_are_refused(self):
        self.assertEqual(sync.sanitize_svg('<!DOCTYPE svg [<!ENTITY a "b">]><svg xmlns="http://www.w3.org/2000/svg"/>')[0], "")
        self.assertEqual(sync.sanitize_svg("<html/>")[0], "")
        self.assertEqual(sync.sanitize_svg("<svg")[0], "")

    def test_clean_input_has_no_problems(self):
        clean, problems = self.clean('<g><text x="1" y="1">hi</text></g>')
        self.assertEqual(problems, [])
        self.assertIn("hi", clean)


class InlineDiagramTests(FixtureCase):
    def pages(self):
        run("render", str(self.dir))
        return {p.stem: p.read_text(encoding="utf-8") for p in (self.dir / "build").glob("*.html")}

    def test_diagrams_are_inline_svg_not_images(self):
        for name, text in self.pages().items():
            with self.subTest(page=name):
                self.assertNotIn("data:image/svg+xml", text)
                self.assertRegex(text, r'<figure class="diagram"><span class="diagram-frame" style="max-width:\d+px"><svg ')
                self.assertNotRegex(text.split("<main>")[1], r"<figure class=\"diagram\">.*?<img")

    def test_unsafe_svg_is_rejected_by_validate_and_stripped_by_render(self):
        path = self.dir / "diagrams" / "concept-c1.svg"
        svg = path.read_text(encoding="utf-8").replace("</defs>", '</defs><script>alert(1)</script><rect onclick="x()" width="1" height="1"/>', 1)
        path.write_text(svg, encoding="utf-8")
        code, codes, out = self.codes()
        self.assertEqual(code, 1)
        self.assertIn("unsafe-svg", codes)
        run("render", str(self.dir), "--tabs", "tab3")
        page = (self.dir / "build" / "tab3.html").read_text(encoding="utf-8")
        self.assertNotIn("alert(1)", page)
        self.assertNotIn("onclick", page)

    def test_diagram_text_is_at_least_11px(self):
        for path in (FIXTURE / "diagrams").glob("*.svg"):
            sizes = [float(n) for n in re.findall(r'font-size="([\d.]+)"', path.read_text(encoding="utf-8"))]
            with self.subTest(svg=path.name):
                self.assertTrue(sizes)
                self.assertGreaterEqual(min(sizes), 11)

    def test_invalid_svg_xml_is_an_error_and_a_placeholder(self):
        (self.dir / "diagrams" / "concept-c1.svg").write_text("<svg", encoding="utf-8")
        self.assertIn("unsafe-svg", self.codes()[1])
        run("render", str(self.dir), "--tabs", "tab3")
        self.assertIn("Diagram not found", (self.dir / "build" / "tab3.html").read_text(encoding="utf-8"))


class UcFragmentTests(FixtureCase):
    def tab4(self):
        run("render", str(self.dir), "--tabs", "tab4")
        return (self.dir / "build" / "tab4.html").read_text(encoding="utf-8")

    def test_one_diagram_per_uc_with_every_flow_as_a_hidden_fragment(self):
        text = self.tab4()
        for uid, expected in (("UC-01", 4), ("UC-02", 4), ("UC-03", 3)):
            section = text.split(f'<section class="uc" id="{uid}">')[1].split("</section>")[0] if False else text.split(f'<section class="uc" id="{uid}">')[1].split('<div class="grid3">')[0]
            with self.subTest(uc=uid):
                self.assertEqual(section.count('<figure class="diagram">'), 1)
                self.assertEqual(len(re.findall(r'data-fragment="[^"]+" data-kind="(?:bad|edge)" display="none"', section)), expected)

    def test_minimum_scenarios_per_uc(self):
        data = json.loads((self.dir / "usecases.json").read_text(encoding="utf-8"))
        counts = {u["id"]: ({k: sum(1 for f in u["flows"] if f["kind"] == k) for k in ("bad", "edge")}) for u in data["ucs"]}
        self.assertGreaterEqual(counts["UC-01"]["bad"], 2)
        self.assertGreaterEqual(counts["UC-01"]["edge"], 2)
        self.assertGreaterEqual(counts["UC-02"]["bad"], 2)
        self.assertGreaterEqual(counts["UC-02"]["edge"], 2)
        self.assertGreaterEqual(counts["UC-03"]["bad"], 1)
        self.assertGreaterEqual(counts["UC-03"]["edge"], 2)

    def test_group_and_per_fragment_toggles_and_one_touchpoint_row_per_scenario(self):
        text = self.tab4()
        section = text.split('<section class="uc" id="UC-01">')[1].split("</section>")[0]
        self.assertIn('data-target="uc-01" data-kind="bad" aria-pressed="false"', section)
        self.assertIn('data-target="uc-01" data-kind="edge" aria-pressed="false"', section)
        self.assertEqual(len(re.findall(r'data-fragment="[^"]+" aria-pressed="false"', section)), 4)
        self.assertEqual(section.count('<tr><td>') - 0, 5)
        self.assertIn("Zero projects", section)

    def test_missing_fragment_and_missing_diagram_are_errors(self):
        path = self.dir / "diagrams" / "uc-01.svg"
        path.write_text(path.read_text(encoding="utf-8").replace('data-fragment="bad-db"', 'data-fragment="renamed"'), encoding="utf-8")
        code, codes, out = self.codes()
        self.assertEqual(code, 1)
        self.assertIn("no-fragment", codes)
        self.assertIn("bad-db", out)
        self.setUp()
        self.edit("usecases.json", lambda d: d["ucs"][0].pop("diagram"))
        self.assertIn("no-diagram", self.codes()[1])

    def test_duplicate_flow_ids_are_an_error(self):
        self.edit("usecases.json", lambda d: d["ucs"][0]["flows"].append(dict(d["ucs"][0]["flows"][1])))
        self.assertIn("duplicate-id", self.codes()[1])

    @unittest.skipUnless(sync.find_browser(), "needs headless Edge or Chrome")
    def test_in_browser_fragments_show_hide_in_place_and_the_diagram_reflows(self):
        probe = """var svg=document.getElementById('uc-01'); var h0=svg.viewBox.baseVal.height;
var kind=document.querySelector('[data-target=uc-01][data-kind=bad]');
kind.click(); var h1=svg.viewBox.baseVal.height;
out.push('grew='+(h1>h0)); out.push('badshown='+svg.querySelectorAll('[data-fragment][data-kind=bad]:not([display=none])').length);
out.push('pressed='+kind.getAttribute('aria-pressed')); out.push('edgeshown='+svg.querySelectorAll('[data-fragment][data-kind=edge]:not([display=none])').length);
var one=document.querySelector('[data-target=uc-01][data-fragment=edge-empty]'); one.click(); var h2=svg.viewBox.baseVal.height;
out.push('one='+(h2>h1)+'/'+one.getAttribute('aria-pressed'));
kind.click(); out.push('collapsed='+(svg.viewBox.baseVal.height<h2));
one.click(); out.push('back='+(svg.viewBox.baseVal.height===h0));
var lines=svg.querySelectorAll('[data-lifeline]'); out.push('lifelines='+(+lines[0].getAttribute('y2')<h0))"""
        result = run_probe(self.tab4(), probe, self.dir / "build" / "probe-frag.html")
        self.assertEqual(result, "grew=true;badshown=2;pressed=true;edgeshown=0;one=true/true;collapsed=true;back=true;lifelines=true")

    @unittest.skipUnless(sync.find_browser(), "needs headless Edge or Chrome")
    def test_in_browser_print_document_shows_every_fragment(self):
        project = sync.load_project(self.dir)
        document = sync.Site(project, print_mode=True).print_document(["tab4"])
        (self.dir / "build").mkdir(exist_ok=True)
        result = run_probe(document, "out.push('hidden='+document.querySelectorAll('svg [data-fragment][display=none]').length);out.push('frags='+document.querySelectorAll('svg [data-fragment]').length)",
                           self.dir / "build" / "probe-print.html")
        self.assertEqual(result, "hidden=0;frags=11")


class ContainerL3Tests(FixtureCase):
    def tab5(self):
        run("render", str(self.dir), "--tabs", "tab5")
        return (self.dir / "build" / "tab5.html").read_text(encoding="utf-8")

    def test_overview_switch_offers_l1_and_l2_only(self):
        text = self.tab5()
        self.assertEqual(re.findall(r'data-level-btn="(L\d)"', text), ["L1", "L2"])
        self.assertEqual(re.findall(r'class="ba-cols" data-level="(L\d)"', text), ["L1", "L2"])

    def test_container_card_holds_the_l3_before_after_with_expand_both(self):
        text = self.tab5()
        card = re.search(r'<details class="card comp unchanged" id="CMP-API"[^>]*>.*?</details>\s*<details', text, re.S).group(0)
        self.assertIn("Inside API (C4 L3)", card)
        self.assertEqual(card.count('<figure class="diagram">'), 2)
        self.assertIn('class="expand-both"', card)
        self.assertIn('class="badge unchanged"', card)
        self.assertNotRegex(card.split(">")[0] + ">", r"\bopen\b")  # compact: collapsed by default
        self.assertNotIn("CMP-API-READ", text.split('id="CMP-API"')[0])  # the L3 component has no card of its own

    def test_l3_view_in_global_views_is_an_error(self):
        self.edit("registry.json", lambda d: d["views"].update(L3={"before": "diagrams/x.svg", "after": "diagrams/y.svg"}))
        code, codes, _ = self.codes()
        self.assertEqual(code, 1)
        self.assertIn("l3-not-global", codes)

    def test_l3_component_must_declare_an_l2_parent(self):
        def comp(d):
            return next(c for c in d["components"] if c["id"] == "CMP-API-READ")
        self.edit("registry.json", lambda d: comp(d).pop("parent"))
        self.assertIn("l3-no-parent", self.codes()[1])
        self.setUp()
        self.edit("registry.json", lambda d: comp(d).update(parent="CMP-NOPE"))
        self.assertIn("l3-bad-parent", self.codes()[1])
        self.setUp()
        self.edit("registry.json", lambda d: comp(d).update(parent="CMP-SYS"))  # an L1 system is not a container
        self.assertIn("l3-bad-parent", self.codes()[1])

    def test_container_with_l3_components_but_no_view_is_a_warning(self):
        def change(d):
            next(c for c in d["components"] if c["id"] == "CMP-API").pop("views")
        self.edit("registry.json", change)
        code, codes, out = self.codes()
        self.assertEqual(code, 0)
        self.assertIn("view-missing", codes)
        self.assertIn("CMP-API", out)


class ComponentDiagramTests(FixtureCase):
    def tab5(self):
        run("render", str(self.dir), "--tabs", "tab5")
        return (self.dir / "build" / "tab5.html").read_text(encoding="utf-8")

    def card(self, text, cid):
        return re.search(rf'<details class="card comp \w+" id="{cid}"[^>]*>.*?</details>\s*(?:<details class="card comp|</section>)', text, re.S).group(0)

    def test_every_changing_component_has_diagrams_at_the_top_of_its_card(self):
        text = self.tab5()
        for cid in ("CMP-SYS", "CMP-WEB", "CMP-DB", "CMP-QUEUE", "CMP-CRON", "CMP-AGG"):
            card = self.card(text, cid)
            with self.subTest(component=cid):
                body = card.split('<div class="comp-body">')[1]
                self.assertTrue(body.lstrip().startswith('<div class="ba-cols"'))
                self.assertLess(body.index("<figure"), body.index("What changes") if "What changes" in body else len(body))

    def test_db_has_an_er_extra_and_queue_a_topology_extra(self):
        text = self.tab5()
        self.assertIn("Schema diff (ER)", self.card(text, "CMP-DB"))
        self.assertIn('id="er-projects-db"', self.card(text, "CMP-DB"))
        self.assertIn("Topology", self.card(text, "CMP-QUEUE"))
        self.assertIn('id="queue-topology"', self.card(text, "CMP-QUEUE"))

    def test_web_app_has_an_optional_freshness_flowchart(self):
        card = self.card(self.tab5(), "CMP-WEB")
        self.assertIn("Freshness label", card)
        self.assertIn('id="flow-freshness"', card)

    def test_missing_component_diagrams_warn_but_flowchart_is_optional(self):
        def change(d):
            comps = {c["id"]: c for c in d["components"]}
            comps["CMP-WEB"]["change"]["diagrams"].pop("flowchart")
            comps["CMP-AGG"]["change"]["diagrams"].pop("before")
            comps["CMP-DB"]["change"]["diagrams"].pop("extra")
        self.edit("registry.json", change)
        code, codes, out = self.codes()
        self.assertEqual(code, 0)
        self.assertEqual(codes - {"th-stale"}, {"component-view-missing", "component-extra-missing"})  # removing the flowchart also changes the translated text
        self.assertEqual([line for line in out.splitlines() if "CMP-WEB" in line and "component-" in line], [])

    def test_flowchart_needs_title_and_diagram(self):
        def change(d):
            next(c for c in d["components"] if c["id"] == "CMP-WEB")["change"]["diagrams"]["flowchart"][0].pop("title")
        self.edit("registry.json", change)
        self.assertIn("flowchart-incomplete", self.codes()[1])

    def test_flowchart_title_has_thai_and_is_in_the_translation_check(self):
        self.edit("registry.json", lambda d: next(c for c in d["components"] if c["id"] == "CMP-WEB")["change"]["diagrams"]["flowchart"][0].update(title="Freshness rules"))
        self.assertIn("th-stale", self.codes()[1])

    def test_er_diagram_shows_tables_columns_keys_and_cardinality(self):
        svg = (FIXTURE / "diagrams" / "comp-cmp-db-extra.svg").read_text(encoding="utf-8")
        for needle in ("users", "projects", "status_history", "status_updated_at", "last_export_at", "project_id", "lead_id", "changed_by", ">PK<", ">FK<", "zero or many", "<circle"):
            self.assertIn(needle, svg)
        self.assertIn("stroke-dasharray", svg)  # a removed column / new relationship is styled


class PairCompareTests(FixtureCase):
    def tab5(self):
        run("render", str(self.dir), "--tabs", "tab5")
        return (self.dir / "build" / "tab5.html").read_text(encoding="utf-8")

    def test_every_pair_has_an_expand_both_button_and_print_has_none(self):
        text = self.tab5()
        pairs = text.count('<div class="ba-cols"')
        self.assertEqual(text.count('class="expand-both" data-compare'), pairs)
        self.assertEqual(pairs, 2 + 6 + 1)  # L1 + L2 overview, six changing components, the API's L3
        project = sync.load_project(self.dir)
        document = sync.Site(project, print_mode=True).print_document(["tab5"])
        self.assertNotIn('class="expand-both"', document)

    def test_expand_both_button_sits_under_the_pair_aligned_right(self):
        text = self.tab5()
        for pair in re.findall(r'<div class="ba-cols"[^>]*>.*?</button>\s*</div>', text, re.S):
            self.assertGreater(pair.index('class="expand-both"'), pair.rindex('<div class="pair-side">'))
            self.assertEqual(pair.count('<div class="pair-side">'), 2)
        self.assertEqual(len(re.findall(r'class="expand-both"', text)), 9)
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.expand-both \{[^}]*grid-column: 1 / -1; justify-self: end;")

    def test_pairs_stack_below_1500px_and_viewer_stacks_in_portrait(self):
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 1500px) { .ba-cols { grid-template-columns: 1fr; } }", css)
        self.assertIn('.viewer-panes[data-layout="stack"] { flex-direction: column; }', css)

    def test_compare_labels_and_two_sync_toggles_are_in_every_page(self):
        text = self.tab5()
        self.assertEqual(len(re.findall(r'data-viewer="sync-zoom" aria-pressed="false"', text)), 1)  # off by default
        self.assertEqual(len(re.findall(r'data-viewer="sync-pan" aria-pressed="false"', text)), 1)
        self.assertNotIn('data-viewer="sync"', text)
        for label in ("Expand both", "ขยายทั้งคู่", "Sync zoom", "ซิงก์ซูม", "Sync pan", "ซิงก์การเลื่อน"):
            self.assertIn(label, text)

    @unittest.skipUnless(sync.find_browser(), "needs headless Edge or Chrome")
    def test_in_browser_compare_zoom_and_pan_sync_independently(self):
        probe = """var btn=document.querySelector('.ba-cols:not([hidden]) .expand-both'); btn.focus(); btn.click();
var v=document.getElementById('viewer'), panes=v.querySelectorAll('.viewer-pane'), imgs=v.querySelectorAll('.viewer-img');
var stages=[panes[0].querySelector('.viewer-stage'), panes[1].querySelector('.viewer-stage')];
var zbtn=document.querySelector('[data-viewer=sync-zoom]'), pbtn=document.querySelector('[data-viewer=sync-pan]');
out.push('open='+(!v.hidden)); out.push('two='+(!panes[1].hidden));
out.push('labels='+Array.prototype.map.call(v.querySelectorAll('.pane-label'),function(l){return l.textContent}).join('/'));
out.push('defaults='+zbtn.getAttribute('aria-pressed')+'/'+pbtn.getAttribute('aria-pressed'));
wheel0=function(){stages[0].dispatchEvent(new WheelEvent('wheel',{deltaY:-300,clientX:200,clientY:200,bubbles:true,cancelable:true}))};
var i0=imgs[0].style.transform, i1=imgs[1].style.transform; wheel0(); out.push('independent_at_start='+(imgs[0].style.transform!==i0 && imgs[1].style.transform===i1));
function parse(i){var m=/translate\(([-\d.e]+)px,\s*([-\d.e]+)px\)\s*scale\(([-\d.e]+)\)/.exec(imgs[i].style.transform);return {x:+m[1],y:+m[2],s:+m[3]}}
function setBtn(b,on){if((b.getAttribute('aria-pressed')==='true')!==on)b.click()}
function drag(i,dx,dy){stages[i].dispatchEvent(new PointerEvent('pointerdown',{pointerId:7,clientX:300,clientY:300,bubbles:true}));document.dispatchEvent(new PointerEvent('pointermove',{pointerId:7,clientX:300+dx,clientY:300+dy,bubbles:true}));document.dispatchEvent(new PointerEvent('pointerup',{pointerId:7,bubbles:true}))}
function wheel(i){stages[i].dispatchEvent(new WheelEvent('wheel',{deltaY:-300,clientX:200,clientY:200,bubbles:true,cancelable:true}))}
function near(a,b){return Math.abs(a-b)<0.01}
[[true,true],[true,false],[false,true],[false,false]].forEach(function(c){
  document.querySelector('[data-viewer=reset]').click();
  setBtn(zbtn,false); setBtn(pbtn,false); drag(1,60,40);
  setBtn(zbtn,c[0]); setBtn(pbtn,c[1]);
  var a0=parse(0), a1=parse(1); wheel(0); var b0=parse(0), b1=parse(1);
  var zoomOk=b0.s>a0.s && (c[0] ? (near(b1.s,b0.s) && !near(b1.x,b0.x)) : (near(b1.s,a1.s) && near(b1.x,a1.x) && near(b1.y,a1.y)));
  drag(0,30,20); var d0=parse(0), d1=parse(1);
  var panOk=near(d0.x-b0.x,30) && near(d0.y-b0.y,20) && near(d0.s,b0.s) && near(d1.s,b1.s) && (c[1] ? (near(d1.x-b1.x,30) && near(d1.y-b1.y,20)) : (near(d1.x,b1.x) && near(d1.y,b1.y)));
  out.push('z'+(+c[0])+'p'+(+c[1])+'=zoom:'+zoomOk+',pan:'+panOk);
});
setBtn(zbtn,false); setBtn(pbtn,false); wheel(0);
document.querySelector('[data-viewer=reset]').click(); out.push('fit='+(imgs[0].style.transform===imgs[1].style.transform));
setBtn(zbtn,true); setBtn(pbtn,true); out.push('relinked='+zbtn.getAttribute('aria-pressed')+'/'+pbtn.getAttribute('aria-pressed'));
document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})); out.push('closed='+v.hidden); out.push('focus='+(document.activeElement===btn));
btn.click(); document.querySelector('[data-viewer=close]').click(); out.push('xclose='+v.hidden);
var one=document.querySelector('.diagram .expand'); one.click(); out.push('single='+(!v.hidden)+'/'+panes[1].hidden+'/'+document.querySelector('[data-viewer=sync-zoom]').hidden); document.querySelector('[data-viewer=close]').click()"""
        result = run_probe(self.tab5(), probe, self.dir / "build" / "probe-compare.html", "1300,1000")
        self.assertEqual(result, "open=true;two=true;labels=Before/After;defaults=false/false;independent_at_start=true;"
                                 "z1p1=zoom:true,pan:true;z1p0=zoom:true,pan:true;z0p1=zoom:true,pan:true;z0p0=zoom:true,pan:true;"
                                 "fit=true;relinked=true/true;closed=true;focus=true;xclose=true;single=true/true/true")


class CompareLayoutToggleTests(FixtureCase):
    def tab5(self):
        run("render", str(self.dir), "--tabs", "tab5")
        return (self.dir / "build" / "tab5.html").read_text(encoding="utf-8")

    def test_segmented_layout_control_markup_and_labels(self):
        text = self.tab5()
        self.assertRegex(text, r'<span class="layout-seg" role="group" aria-label="Layout[^"]*" hidden>')
        for name in ("layout-side", "layout-stack"):
            self.assertRegex(text, rf'data-viewer="{name}" aria-pressed="(?:true|false)"')
        for label in ("Side by side", "เคียงข้างกัน", "Top / bottom", "บน / ล่าง"):
            self.assertIn(label, text)

    def test_script_remembers_the_choice_in_try_catch_and_refits(self):
        js = (SKILL / "templates" / "base.js").read_text(encoding="utf-8")
        self.assertIn("ga-viewer-layout", js)
        self.assertRegex(js, r"try \{[^}]*localStorage\.setItem\(LAYOUT_KEY")
        self.assertIn("data-layout", (SKILL / "templates" / "base.css").read_text(encoding="utf-8"))
        self.assertRegex(js, r"function applyLayout\(refit\)")

    @unittest.skipUnless(sync.find_browser(), "needs headless Edge or Chrome")
    def test_in_browser_auto_choice_toggle_refit_and_memory(self):
        probe = """var v=document.getElementById('viewer'), panes=v.querySelector('.viewer-panes');
function open(){var b=document.querySelector('.ba-cols:not([hidden]) .expand-both'); b.click(); return b}
function r(i){return v.querySelectorAll('.viewer-pane')[i].getBoundingClientRect()}
function side(){return r(0).right<=r(1).left+1 && Math.abs(r(0).top-r(1).top)<2}
function stack(){return r(1).top>=r(0).bottom-1 && Math.abs(r(0).left-r(1).left)<2}
function btn(n){return document.querySelector('[data-viewer=layout-'+n+']')}
function tf(i){return v.querySelectorAll('.viewer-img')[i].style.transform}
var trigger=open();
out.push('auto='+panes.dataset.layout+'/'+side()+'/'+btn('side').getAttribute('aria-pressed')+'/'+btn('stack').getAttribute('aria-pressed'));
var before=tf(0); btn('stack').click();
out.push('stack='+panes.dataset.layout+'/'+stack()+'/'+btn('stack').getAttribute('aria-pressed')+'/'+btn('side').getAttribute('aria-pressed'));
out.push('refit='+(tf(0)!==before)); out.push('saved='+localStorage.getItem('ga-viewer-layout'));
document.querySelector('[data-viewer=close]').click(); open();
out.push('remembered='+panes.dataset.layout+'/'+stack());
btn('side').click(); out.push('side='+panes.dataset.layout+'/'+side()+'/'+localStorage.getItem('ga-viewer-layout'));
document.querySelector('[data-viewer=close]').click();
localStorage.removeItem('ga-viewer-layout'); open(); out.push('cleared='+panes.dataset.layout);
document.querySelector('[data-viewer=close]').click();
var one=document.querySelector('.diagram .expand'); one.click(); out.push('single_hidden='+document.querySelector('.layout-seg').hidden); document.querySelector('[data-viewer=close]').click()"""
        wide = run_probe(self.tab5(), probe, self.dir / "build" / "probe-layout.html", "1300,1000")
        self.assertEqual(wide, "auto=side/true/true/false;stack=stack/true/true/false;refit=true;saved=stack;remembered=stack/true;side=side/true/side;cleared=side;single_hidden=true")
        narrow = run_probe(self.tab5(), "var b=document.querySelector('.ba-cols:not([hidden]) .expand-both'); b.click(); out.push('narrow='+document.querySelector('.viewer-panes').dataset.layout)",
                           self.dir / "build" / "probe-layout2.html", "700,1000")
        self.assertEqual(narrow, "narrow=stack")

class ConceptPlainTests(FixtureCase):
    def tab3(self):
        run("render", str(self.dir), "--tabs", "tab3")
        return (self.dir / "build" / "tab3.html").read_text(encoding="utf-8")

    def block(self, cid="C1"):
        return self.tab3().split(f'<article class="card blk" id="{cid}">')[1].split("</article>")[0]

    def test_header_order_title_one_line_card_analogy_then_diagram_then_technical_text(self):
        block = self.block()
        order = [block.index(x) for x in ("<h2>", 'class="oneline"', 'class="plain-card"', 'class="analogy"', 'class="dg"', 'class="tx"')]
        self.assertEqual(order, sorted(order))

    def test_one_line_is_large_and_in_plain_words(self):
        block = self.block()
        self.assertIn("When a lead changes a status, everyone sees it within seconds instead of waiting for the weekly sheet.", block)
        self.assertIn("In one line", block)
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.oneline p?[^{]*\{[^}]*font-size: (?:2\d|3\d)px")

    def test_three_row_before_now_why_card(self):
        card = re.search(r'<dl class="plain-card">.*?</dl>', self.block(), re.S).group(0)
        text = re.sub(r"<[^>]+>", "", card)
        for label in ("Before", "Now", "Why it matters"):
            self.assertIn(label, text)
        self.assertEqual(card.count("<dt>"), 3)
        self.assertIn("weekly sheet", text)
        self.assertIn("in seconds", text)

    def test_analogy_is_optional(self):
        self.assertIn("Think of it like", self.block())
        self.assertIn("group chat notification", self.block())
        self.edit("concepts.json", lambda d: d["concepts"][0].pop("analogy"))
        self.assertNotIn("Think of it like", self.block())
        self.assertNotIn("concept-plain-missing", self.codes()[1])

    def test_kind_label_sits_next_to_the_title_and_side_index_shows_the_plain_line(self):
        text = self.tab3()
        self.assertIn('<h2>Event-driven status <span class="kind">new pattern</span></h2>', re.sub(r"<span data-l=\"th\">.*?</span>", "", re.sub(r"<span data-l=\"en\">(.*?)</span>", r"\1", text)))
        side = text.split('<aside class="side">')[1].split("</aside>")[0]
        self.assertIn("When a lead changes a status, everyone sees it within seconds", side)
        self.assertNotIn("new pattern", side)
        self.assertIn('class="side-sub"', side)

    def test_missing_plain_is_a_warning_and_side_falls_back_to_the_kind_label(self):
        self.edit("concepts.json", lambda d: d["concepts"][1].pop("plain"))
        code, codes, out = self.codes()
        self.assertEqual(code, 0)
        self.assertIn("concept-plain-missing", codes)
        self.assertIn("C2", out)
        side = self.tab3().split('<aside class="side">')[1].split("</aside>")[0]
        self.assertIn("new component", side)

    def test_thai_exists_for_every_plain_field_and_data_rows_are_not_translated(self):
        text = self.tab3()
        for needle in ("เมื่อหัวหน้าเปลี่ยนสถานะ", "ลองนึกถึง", "สรุปหนึ่งบรรทัด", "ทำไมจึงสำคัญ"):
            self.assertIn(needle, text)
        entries = sync.content_entries(sync.load_project(self.dir))
        for key in ("plain", "before", "now", "why", "analogy"):
            self.assertIn(key, entries["C1"])
        self.assertEqual([p for p in entries["CMP-QUEUE"] if "infra" in p], [])
        self.assertEqual([p for p in entries["CMP-DB"] if "example" in p or "samples" in p], [])

class OptionCardContrastTests(FixtureCase):
    def test_option_card_css_uses_surface_and_normal_text(self):
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.opt\.chosen, \.opt\.open, \.opt\.deferred, \.opt\.rejected \{ background: var\(--surface\); color: var\(--fg\); \}")
        self.assertRegex(css, r"\.opt\.chosen \{ border: 2px solid var\(--chosen-bd\); \}")

    @unittest.skipUnless(sync.find_browser(), "needs headless Edge or Chrome")
    def test_in_browser_every_option_state_and_badge_has_readable_contrast(self):
        run("render", str(self.dir), "--tabs", "tab2")
        page = (self.dir / "build" / "tab2.html").read_text(encoding="utf-8")
        probe = """function bg(el){while(el){var c=getComputedStyle(el).backgroundColor; if(c&&c!=='rgba(0, 0, 0, 0)'&&c!=='transparent')return c; el=el.parentElement} return 'rgb(255, 255, 255)'}
document.querySelectorAll('details.decision').forEach(function(d){d.open=true});
document.querySelectorAll('.opt, .opt .badge').forEach(function(el){
 var st=(el.className.match(/(chosen|open|deferred|rejected)/)||[])[1];
 out.push((el.classList.contains('badge')?'badge':'opt')+'|'+st+'|'+getComputedStyle(el).color+'|'+bg(el))})"""
        samples = run_probe(page, probe, self.dir / "build" / "probe-contrast.html").split(";")
        seen = set()
        for sample in samples:
            kind, state, fg, bg = sample.split("|")
            rgb = lambda s: [int(float(x)) for x in re.findall(r"[\d.]+", s)[:3]]  # noqa: E731
            ratio = contrast(rgb(fg), rgb(bg))
            seen.add((kind, state))
            with self.subTest(kind=kind, state=state):
                self.assertGreaterEqual(ratio, 4.5, f"{kind} {state}: {fg} on {bg} = {ratio:.2f}")
        self.assertTrue({("opt", "chosen"), ("opt", "rejected"), ("opt", "deferred"), ("opt", "open"), ("badge", "chosen")} <= seen)


class LayoutRuleTests(FixtureCase):
    def test_concept_block_is_header_then_full_width_diagram_then_text(self):
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.blk \{ display: flex; flex-direction: column;")
        self.assertRegex(css, r"\.blk \.tx \{[^}]*max-width: 70ch")
        run("render", str(self.dir), "--tabs", "tab3")
        text = (self.dir / "build" / "tab3.html").read_text(encoding="utf-8")
        block = text.split('<article class="card blk" id="C1">')[1].split("</article>")[0]
        self.assertLess(block.index("<header"), block.index('<div class="dg">'))
        self.assertLess(block.index('<div class="dg">'), block.index('<div class="tx">'))
        self.assertIn("<h2>", block.split('<div class="dg">')[0])

    def test_diagram_frames_fill_the_column_up_to_natural_width(self):
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.diagram-frame \{[^}]*width: 100%")
        self.assertRegex(css, r"\.diagram-frame > svg \{[^}]*width: 100%[^}]*cursor: zoom-in")

    def test_decision_option_grid_is_at_least_280px_and_wireframes_are_expandable(self):
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        self.assertIn("minmax(280px, 1fr)); gap: 12px; margin: 10px 0; }", css)
        run("render", str(self.dir), "--tabs", "tab2")
        text = (self.dir / "build" / "tab2.html").read_text(encoding="utf-8")
        d3 = text.split('id="D3"')[1]
        self.assertGreaterEqual(d3.count('class="expand"'), 2)

class FilesChangedTests(FixtureCase):
    def card(self, cid):
        run("render", str(self.dir), "--tabs", "tab5")
        text = (self.dir / "build" / "tab5.html").read_text(encoding="utf-8")
        return re.search(rf'<details class="card comp \w+" id="{cid}"[^>]*>.*?</details>\s*(?:<details class="card comp|</section>)', text, re.S).group(0)

    def test_summary_line_counts_each_change_type(self):
        for cid, expected in (("CMP-AGG", "Files changed (4): 4 added"), ("CMP-CRON", "Files changed (1): 1 removed"),
                              ("CMP-WEB", "Files changed (5): 2 added, 1 modified, 1 removed, 1 renamed"),
                              ("CMP-DB", "Files changed (4): 3 added, 1 modified")):
            with self.subTest(component=cid):
                self.assertIn(expected, re.sub(r"<[^>]+>", "", self.card(cid)).replace("\n", " "))

    def test_thai_summary_and_labels(self):
        text = re.sub(r"<[^>]+>", "", self.card("CMP-WEB"))
        self.assertIn("ไฟล์ที่เปลี่ยน (5): เพิ่ม 2, แก้ไข 1, ลบ 1, เปลี่ยนชื่อ 1", text)

    def test_each_file_has_a_theme_colored_badge_and_a_marker(self):
        card = self.card("CMP-WEB")
        for status, marker in (("new", "+"), ("changed", "~"), ("removed", "-")):
            self.assertRegex(card, rf'<span class="badge {status}"><span aria-hidden="true">{re.escape(marker)}</span>')
        self.assertIn("web/src/pages/Project.tsx", card)  # old path of the rename
        self.assertIn("web/src/pages/ProjectPage.tsx", card)
        self.assertRegex(card, r'<span class="badge changed"><span aria-hidden="true">&rarr;</span>')

    def test_removed_file_path_is_struck_through_by_css_class(self):
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.file-removed code \{[^}]*line-through")
        self.assertIn('class="file-removed"', self.card("CMP-CRON"))

    def test_plain_string_files_still_work_as_modified_with_a_warning(self):
        def change(d):
            next(c for c in d["components"] if c["id"] == "CMP-CRON")["change"]["files"] = ["jobs/weekly_export.py", "jobs/other.py"]
        self.edit("registry.json", change)
        code, codes, out = self.codes()
        self.assertEqual(code, 0)
        self.assertIn("file-change-type-missing", codes)
        self.assertIn("CMP-CRON", out)
        self.assertIn("Files changed (2): 2 modified", re.sub(r"<[^>]+>", "", self.card("CMP-CRON")).replace("\n", " "))

    def test_bad_change_type_and_rename_without_from_are_errors(self):
        def change(d):
            files = next(c for c in d["components"] if c["id"] == "CMP-AGG")["change"]["files"]
            files[0]["change"] = "deleted"
            files[1].update(change="renamed")
        self.edit("registry.json", change)
        code, codes, _ = self.codes()
        self.assertEqual(code, 1)
        self.assertIn("bad-file-change", codes)
        self.assertIn("file-rename-no-from", codes)

    def test_fixture_has_no_file_warnings_and_print_keeps_the_list_open(self):
        self.assertNotIn("file-change-type-missing", self.codes()[1])
        project = sync.load_project(self.dir)
        document = sync.Site(project, print_mode=True).print_document(["tab5"])
        self.assertEqual(re.findall(r"<details(?![^>]*\bopen\b)[^>]*>", document), [])

class DataDictionaryTests(FixtureCase):
    def db_card(self):
        run("render", str(self.dir), "--tabs", "tab5")
        text = (self.dir / "build" / "tab5.html").read_text(encoding="utf-8")
        return re.search(r'<details class="card comp \w+" id="CMP-DB"[^>]*>.*?</details>\s*(?:<details class="card comp|</section>)', text, re.S).group(0)

    def test_one_collapsed_dictionary_per_new_or_changed_table_under_the_migration_table(self):
        card = self.db_card()
        self.assertEqual(re.findall(r'<details class="dict" >', card), ['<details class="dict" >'] * 2)
        self.assertLess(card.index("Data change"), card.index('class="dictionary"'))
        self.assertLess(card.index('class="dictionary"'), card.index("Infra change"))
        summaries = re.findall(r'<details class="dict" >\s*<summary>(.*?)</summary>', card, re.S)
        self.assertIn("projects", summaries[0])
        self.assertIn("status_history", summaries[1])

    def test_enum_values_are_described_one_by_one(self):
        card = self.db_card()
        for value, meaning in (("on_track", "Work is going as planned."), ("at_risk", "May miss the date unless something changes."),
                               ("blocked", "Cannot continue until something is resolved."), ("done", "Finished.")):
            self.assertRegex(card, rf"<code>{value}</code> = [^\n]*{re.escape(meaning)}")

    def test_columns_show_type_nullable_default_meaning_and_samples(self):
        card = re.sub(r"<[^>]+>", " ", self.db_card())
        for needle in ("status_updated_at", "timestamptz", "now()", "2026-11-15T09:41:07Z", "gen_random_uuid()", "Who made the change; empty for system changes."):
            self.assertIn(needle, card)
        self.assertIn("Column", card)
        self.assertIn("Nullable", card)
        self.assertIn("Default", card)
        self.assertIn("Allowed values", card)

    def test_changed_column_shows_a_before_to_after_example(self):
        card = self.db_card()
        self.assertRegex(card, r"<code>status</code>: <code>&#x27;green&#x27; \(text\)</code> &rarr; <code>&#x27;on_track&#x27; \(enum\)</code>")

    def test_each_table_has_three_sample_rows(self):
        card = self.db_card()
        tables = re.findall(r"<span class=\"lbl\"><span data-l=\"en\">Sample rows</span>.*?</table>", card, re.S)
        self.assertEqual(len(tables), 2)
        for table in tables:
            self.assertEqual(table.count("<td>") // table.count("<th>"), 3)
        self.assertIn("<th>changed_by</th>", tables[1])

    def test_thai_meanings_but_samples_stay_data(self):
        run("render", str(self.dir), "--tabs", "tab5")
        text = (self.dir / "build" / "tab5.html").read_text(encoding="utf-8")
        self.assertIn("งานเป็นไปตามแผน", text)
        entries = sync.content_entries(sync.load_project(self.dir))["CMP-DB"]
        self.assertTrue(any(path.endswith("values.0.meaning") for path in entries))
        self.assertFalse(any("samples" in path or "sample_rows" in path or path.endswith(".default") for path in entries))

    def test_new_or_changed_column_without_a_meaning_is_a_warning(self):
        def change(d):
            table = next(c for c in d["components"] if c["id"] == "CMP-DB")["change"]["data_dictionary"]["tables"][1]
            table["columns"][2].pop("meaning")
        self.edit("registry.json", change)
        code, codes, out = self.codes()
        self.assertEqual(code, 0)
        self.assertIn("column-meaning-missing", codes)
        self.assertIn("status_history.status", out)

    def test_removed_column_needs_no_meaning_and_fixture_is_clean(self):
        def change(d):
            table = next(c for c in d["components"] if c["id"] == "CMP-DB")["change"]["data_dictionary"]["tables"][0]
            table["columns"].append({"name": "last_export_at", "change": "removed", "type": "timestamptz"})
        self.edit("registry.json", change)
        self.assertNotIn("column-meaning-missing", self.codes()[1])

    def test_print_opens_every_dictionary_table(self):
        document = sync.Site(sync.load_project(self.dir), print_mode=True).print_document(["tab5"])
        self.assertEqual(len(re.findall(r'<details class="dict" open>', document)), 2)

def _node_supports_cdp_helper():
    import subprocess
    node = shutil.which("node")
    if not node:
        return False
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20).stdout.strip().lstrip("v")
        return int(out.split(".")[0]) >= 22  # global WebSocket
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


@unittest.skipUnless(sync.find_browser() and _node_supports_cdp_helper(), "needs headless Edge/Chrome and Node 22+")
class BrowserCdpTests(unittest.TestCase):
    """Real-browser checks over the DevTools protocol (tests/browser/cdp_check.mjs): phone emulation, real touch events, Thai rendering.
    Set CDP_SHOTS_DIR to keep the screenshots; otherwise they go to a temp folder that is removed afterwards."""

    results = {}
    shots = []

    @classmethod
    def setUpClass(cls):
        import subprocess
        cls.tmp = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        artifact = cls.tmp / "artifact"
        shutil.copytree(FIXTURE, artifact, ignore=shutil.ignore_patterns("build"))
        run("render", str(artifact))
        shots = Path(os.environ.get("CDP_SHOTS_DIR") or cls.tmp / "shots")
        script = REPO / "tests" / "browser" / "cdp_check.mjs"
        done = subprocess.run(["node", str(script), "--browser", sync.find_browser(), "--pages", str(artifact / "build"), "--shots", str(shots)],
                              capture_output=True, text=True, encoding="utf-8", timeout=300)
        line = next((ln for ln in reversed(done.stdout.splitlines()) if ln.startswith("RESULT:")), None)
        if line is None:
            raise AssertionError("cdp_check.mjs produced no result (exit %s):" % done.returncode + chr(10) + "stdout: " + done.stdout[-400:] + chr(10) + "stderr: " + done.stderr[-800:])
        cls.results = json.loads(line[len("RESULT:"):])
        cls.shots = cls.results.pop("_shots", [])

    def assert_group(self, prefix, expected_at_least):
        group = {name: r for name, r in self.results.items() if name.startswith(prefix)}
        self.assertGreaterEqual(len(group), expected_at_least, f"only {len(group)} {prefix}* checks ran")
        for name, outcome in sorted(group.items()):
            with self.subTest(check=name):
                self.assertTrue(outcome["ok"], f"{name}: {outcome['detail']}")

    def test_harness_ran_cleanly(self):
        self.assertNotIn("harness.error", self.results, self.results.get("harness.error", {}).get("detail"))

    def test_phone_layout_on_all_six_pages(self):
        self.assert_group("phone.", 6 + 1 + 2 + 2 + 3 + 1 + 3 + 1 + 3 + 2 + 3)  # overflow x6, tab bar, side index x2, pairs x2, tables x3, viewer bar, cards x3, crud scroll box
        for page in ("intent", "tab1", "tab2", "tab3", "tab4", "tab5"):
            self.assertIn(f"phone.no_horizontal_overflow.{page}", self.results)

    def test_phone_360_and_390_pages_never_scroll_sideways(self):
        self.assert_group("phone360.", 6)
        for page in ("intent", "tab1", "tab2", "tab3", "tab4", "tab5"):
            for prefix in ("phone", "phone360"):
                self.assertTrue(self.results[f"{prefix}.no_horizontal_overflow.{page}"]["ok"])
        self.assertTrue(any("phone360-tab1" in shot for shot in self.shots))

    def test_real_touch_viewer_pinch_drag_backdrop_and_compare(self):
        self.assert_group("touch.", 11)

    def test_viewer_layout_toggle_on_desktop(self):
        self.assert_group("desktop.", 5)

    def test_thai_glyphs_render_and_nothing_overflows(self):
        self.assert_group("thai.", 2 + 12)

    def test_screenshots_were_saved(self):
        self.assertGreaterEqual(len(self.shots), 15)
        for shot in self.shots:
            self.assertTrue(Path(shot).is_file() and Path(shot).stat().st_size > 1000, shot)

class MobileTableTests(FixtureCase):
    def pages(self):
        run("render", str(self.dir))
        return {p.stem: p.read_text(encoding="utf-8") for p in (self.dir / "build").glob("*.html")}

    def test_every_table_on_every_page_sits_in_a_scroll_box(self):
        for name, text in self.pages().items():
            with self.subTest(page=name):
                self.assertEqual(text.count("<table"), len(re.findall(r'<div class="table-wrap[^"]*"><table', text)))
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.table-wrap \{[^}]*overflow-x: auto")

    def test_key_tables_turn_into_labelled_cards_on_narrow_screens(self):
        pages = self.pages()
        for page, marker in (("tab1", '<table class="uc-table stack">'), ("tab1", '<table class="stack">'), ("tab4", '<table class="stack">'), ("tab5", '<table class="dict-cols stack">')):
            self.assertIn(marker, pages[page])
        for page in ("tab1", "tab4", "tab5"):
            for table in re.findall(r'<table class="[^"]*stack[^"]*">.*?</table>', pages[page], re.S):
                rows = re.findall(r"<tr>.*?</tr>", table, re.S)
                self.assertTrue(rows)
                for row in rows:
                    cells = re.findall(r"<td[^>]*>.*?</td>", row, re.S)
                    self.assertTrue(cells and all('class="cell-label"' in c for c in cells), row[:120])
                self.assertIn('<tr class="stack-head">', table)
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        phone = css.split("@media (max-width: 640px)")[1].split("\n}\n")[0]
        for needle in ("table.stack", ".stack-head", ".cell-label"):
            self.assertIn(needle, phone)
        self.assertRegex(css, r"\.cell-label \{ display: none; \}")  # hidden on wide screens

    def test_crud_matrix_stays_a_scroll_box_with_a_sticky_first_column(self):
        text = self.pages()["tab1"]
        self.assertIn('<table class="crud-table">', text)
        self.assertNotIn("crud-table stack", text)
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.crud-table th:first-child, \.crud-table td:first-child \{[^}]*position: sticky; left: 0")

    def test_width_audit_rules(self):
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        for rule in (r"\.card[^{]*\{[^}]*min-width: 0", r"overflow-wrap: anywhere", r"svg[^{]*\{[^}]*max-width: 100%", r"body \{[^}]*overflow-x: clip",
                     r"\.nav \{[^}]*overflow-x: auto"):
            self.assertRegex(css, rule)

class FlipTableTests(FixtureCase):
    def pages(self):
        run("render", str(self.dir))
        return {p.stem: p.read_text(encoding="utf-8") for p in (self.dir / "build").glob("*.html")}

    def test_every_key_table_is_rendered_twice_from_the_same_rows(self):
        pages = self.pages()
        uc = re.search(r'<table class="uc-table stack">.*?</table>', pages["tab1"], re.S).group(0)
        flip = re.search(r'<table class="flip">.*?</table>', pages["tab1"], re.S).group(0)
        self.assertEqual(flip.count('<th scope="row">'), 5)  # one row per field
        for row in re.findall(r"<tr><th scope=\"row\">.*?</tr>", flip, re.S):
            self.assertEqual(row.count("<td>"), 3)  # one column per item (UC)
        for needle in ("UC-01", "UC-03", "A team member opens the status page", "via", "Open in Tab 4"):
            self.assertIn(needle, re.sub(r"<[^>]+>", " ", flip))
            self.assertIn(needle, re.sub(r"<[^>]+>", " ", uc))
        for page, expected in (("tab1", 2), ("tab4", 3), ("tab5", 2)):
            self.assertEqual(pages[page].count('<table class="flip">'), expected, page)
            self.assertEqual(len(re.findall(r'<table class="[^"]*stack[^"]*">', pages[page])), expected, page)

    def test_toggle_markup_defaults_to_flipped(self):
        text = self.pages()["tab1"]
        self.assertEqual(text.count('data-table-view="flip" aria-pressed="true"'), 2)
        self.assertEqual(text.count('data-table-view="cards" aria-pressed="false"'), 2)
        for label in ("Flip", "สลับแกน", "Cards", "การ์ด", "swipe", "ปัดดู"):
            self.assertIn(label, text)

    def test_crud_matrix_and_sample_rows_are_not_flipped(self):
        pages = self.pages()
        self.assertNotIn("tblset", pages["tab1"].split('<table class="crud-table">')[1].split("</table>")[0])
        tab5 = pages["tab5"]
        self.assertEqual(tab5.count('<table class="flip">'), 2)  # only the two dictionary column tables, not the sample rows

    def test_css_flips_only_on_narrow_screens_with_snap_sticky_label_and_fade(self):
        css = (SKILL / "templates" / "base.css").read_text(encoding="utf-8")
        wide = css.split("@media (max-width: 640px)")[0]
        self.assertRegex(wide, r"\.tbl-flip \{ display: none; \}")
        self.assertRegex(wide, r"\.view-toggle \{ display: none; \}")
        narrow = css.split("@media (max-width: 640px)")[-1]
        for needle in ('html[data-table-view="cards"] .tbl-normal', ".tbl-flip", "scroll-snap-type: x mandatory", "scroll-snap-align: start", "position: sticky",
                       ".swipe-hint", "linear-gradient(to right, transparent, var(--surface))"):
            self.assertIn(needle, css)
        self.assertRegex(narrow, r"\.tbl-normal \{ display: none; \}")
        self.assertIn("body.print .tbl-flip", css)

    def test_script_remembers_the_choice_globally_in_try_catch(self):
        js = (SKILL / "templates" / "base.js").read_text(encoding="utf-8")
        self.assertIn("ga-table-view", js)
        self.assertRegex(js, r"try \{[^}]*localStorage\.setItem\(TABLE_VIEW_KEY")
        self.assertIn("flip-scroll", js)

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
