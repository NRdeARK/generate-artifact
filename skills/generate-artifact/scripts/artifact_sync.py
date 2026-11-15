#!/usr/bin/env python3
"""artifact_sync: validate, render and export generate-artifact project data.

Stdlib only. Never touches the network or claude.ai: publishing is done by
Claude with the artifact tools (see SKILL.md), which then updates artifacts.json.

    artifact_sync.py validate   <artifact_dir> [--strict]
    artifact_sync.py render     <artifact_dir> [--tabs intent tab1 ...] [--dry-run]
    artifact_sync.py export-pdf <artifact_dir> [--lang en|th] [--dry-run]
    artifact_sync.py i18n-diff  <artifact_dir>
"""
import argparse
import base64
import hashlib
import html
import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

if sys.version_info < (3, 9):
    sys.exit("artifact_sync needs Python 3.9 or newer. Run it with a newer interpreter.")

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"
THEMES = SKILL_DIR / "themes"
DEFAULT_THEME = "anthropic"
I18N = SKILL_DIR / "i18n"

TABS = ["intent", "tab1", "tab2", "tab3", "tab4", "tab5"]
TAB_DATA_FILES = {
    "intent": "intent.md",
    "tab1": "usecases.json",
    "tab2": "decisions.json",
    "tab3": "concepts.json",
    "tab4": "usecases.json",
    "tab5": "registry.json",
}
C4_LEVELS = ("L1", "L2", "L3")
CHANGE_STATUSES = ("unchanged", "changed", "new", "removed")
OPTION_STATUSES = ("chosen", "deferred", "rejected", "open")
TIERS = ("Main", "Affected")
FLOW_KINDS = ("good", "bad", "edge")
CRUD_OPS = ("create", "read", "update", "delete")
INTENT_SECTIONS = {"what": "What", "why": "Why", "before": "Before state",
                   "constraints": "Constraints", "outcome": "Outcome success"}
INTENT_DIAGRAM = "diagrams/intent.svg"
# Only these keys hold translatable prose; IDs, names, enums and code are never translated.
TEXT_KEYS = {"title", "summary", "what", "why", "reason", "pros", "cons", "cost", "risk",
             "sees", "terms", "label", "choice_reason", "revisit_when", "text"}

log = logging.getLogger("artifact_sync")
esc = html.escape


class DataError(Exception):
    """A project data file is unreadable or invalid. The message says what to fix."""


# --------------------------------------------------------------------------- data

@dataclass
class Project:
    root: Path
    intent: dict
    registry: dict
    usecases: dict
    decisions: dict
    concepts: dict
    th: dict
    artifacts: dict

    @property
    def components(self):
        return self.registry["components"]

    @property
    def ucs(self):
        return self.usecases["ucs"]

    @property
    def decision_list(self):
        return self.decisions["decisions"]

    @property
    def concept_list(self):
        return self.concepts["concepts"]

    @property
    def name(self):
        return self.artifacts.get("project", "Untitled project")


def read_json(path, list_keys=()):
    """Read a JSON object. list_keys must hold lists of objects (null becomes []); other shapes raise DataError."""
    if not path.is_file():
        return {key: [] for key in list_keys}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DataError(f"{path}: invalid JSON (line {exc.lineno}, col {exc.colno}: {exc.msg}). "
                        "Fix the file and re-run.") from exc
    if not isinstance(data, dict):
        raise DataError(f"{path}: top level must be a JSON object, found {type(data).__name__}. Wrap the content in an object.")
    for key in list_keys:
        value = data.get(key)
        if value is None:
            data[key] = []
        elif not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise DataError(f"{path}: '{key}' must be a list of objects. Fix the field and re-run.")
    return data


def parse_intent(text):
    """Split intent.md into {section_key: text} using the '## Heading' lines."""
    sections, current = {}, None
    for line in text.splitlines():
        heading = re.match(r"##\s+(.+?)\s*$", line)
        if heading:
            current = next((k for k, v in INTENT_SECTIONS.items() if v.lower() == heading.group(1).lower()), None)
            if current:
                sections[current] = []
            continue
        if current:
            sections[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def load_project(root):
    root = Path(root)
    if not root.is_dir():
        raise DataError(f"{root} is not a directory. Pass the project's docs/artifact folder.")
    intent_file = root / "intent.md"
    return Project(
        root=root,
        intent=parse_intent(intent_file.read_text(encoding="utf-8")) if intent_file.is_file() else {},
        registry=read_json(root / "registry.json", ["components"]),
        usecases=read_json(root / "usecases.json", ["outcomes", "ucs", "crud"]),
        decisions=read_json(root / "decisions.json", ["decisions"]),
        concepts=read_json(root / "concepts.json", ["concepts"]),
        th=read_json(root / "i18n" / "th.json"),
        artifacts=_artifacts(root / "artifacts.json"),
    )


def _artifacts(path):
    data = read_json(path)
    data["tabs"] = data.get("tabs") or {}
    if not isinstance(data["tabs"], dict):
        raise DataError(f"{path}: 'tabs' must be an object keyed by intent, tab1..tab5.")
    return data


def present_tabs(root):
    return [tab for tab in TABS if (Path(root) / TAB_DATA_FILES[tab]).is_file()]


# --------------------------------------------------------------------------- i18n entries

def flatten_text(obj, path=""):
    """Collect translatable strings as {dotted.path: text}."""
    out = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            sub = f"{path}.{key}" if path else key
            if isinstance(value, str):
                if key in TEXT_KEYS:
                    out[sub] = value
            else:
                out.update(flatten_text(value, sub))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            out.update(flatten_text(value, f"{path}.{index}" if path else str(index)))
    return out


def content_entries(project):
    """{entry_key: {path: english_text}} for every translatable entry in the project."""
    entries = {"intent": dict(project.intent)} if project.intent else {}
    sources = [project.usecases["outcomes"], project.ucs, project.decision_list, project.concept_list,
               [{"id": c.get("id"), **c["change"]} for c in project.components if c.get("change")]]
    for items in sources:
        for item in items:
            text = flatten_text({k: v for k, v in item.items() if k != "id"})
            if item.get("id") and text:
                entries[item["id"]] = text
    return entries


def source_hash(text):
    return hashlib.sha1(json.dumps(text, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def th_status(project):
    """[(key, 'missing'|'stale', english_text)] for entries whose Thai is absent or out of date."""
    result = []
    for key, text in content_entries(project).items():
        th = project.th.get(key)
        if th is None or any(path not in th.get("text", {}) for path in text):
            result.append((key, "missing", text))
        elif th.get("source_hash") != source_hash(text):
            result.append((key, "stale", text))
    return result


# --------------------------------------------------------------------------- validation

@dataclass
class Finding:
    severity: str  # error | warning
    code: str
    where: str
    message: str

    def __str__(self):
        return f"[{self.severity.upper()}] {self.code:<20} {self.where}: {self.message}"


ID_PATTERNS = {
    "component": r"CMP-[A-Za-z0-9][A-Za-z0-9-]*",
    "uc": r"UC-\d+",
    "decision": r"D\d+",
    "concept": r"C\d+",
    "outcome": r"O\d+",
}
CHANGING = ("changed", "new", "removed")
ARTIFACT_HOSTS = ("claude.ai",)


def safe_url(url):
    """True only for https URLs on an artifact host. Anything else is never linked."""
    if not isinstance(url, str):
        return False
    parts = urlparse(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in ARTIFACT_HOSTS)


def resolve_diagram(root, rel):
    """(path, problem). problem is set unless rel is an .svg file path inside root. Used by validator and renderer."""
    path = Path(root) / rel
    if path.suffix.lower() != ".svg":
        return None, "is not an .svg file"
    if not path.resolve().is_relative_to(Path(root).resolve()):
        return None, "points outside the project folder"
    return path, None


def _unknown(file, where, refs, known, kind):
    return [Finding("error", "unknown-id", f"{file} {where}",
                    f"{kind} '{ref}' is not defined. Add it to its source file or fix the ID.")
            for ref in refs or [] if ref not in known]


def check_ids(file, items, kind):
    """Every item needs an ID of the right shape, unique within its collection."""
    out, seen = [], set()
    for index, item in enumerate(items, start=1):
        ident, where = item.get("id"), f"{file} item {index}"
        if not ident:
            out.append(Finding("error", "missing-id", where, f"{kind} has no id. Give it a stable ID."))
        elif not isinstance(ident, str) or not re.fullmatch(ID_PATTERNS[kind], ident):
            out.append(Finding("error", "bad-id", where, f"{kind} id {ident!r} must match {ID_PATTERNS[kind]}."))
        elif ident in seen:
            out.append(Finding("error", "duplicate-id", f"{file} {ident}", "ID used twice. IDs must be unique."))
        seen.add(ident)
    return out


def known_ids(project):
    return {
        "component": {c.get("id") for c in project.components},
        "uc": {u.get("id") for u in project.ucs},
        "decision": {d.get("id") for d in project.decision_list},
    }


def check_registry(project):
    known, out = known_ids(project), check_ids("registry.json", project.components, "component")
    for comp in project.components:
        cid = comp.get("id", "?")
        if comp.get("c4_level") not in C4_LEVELS:
            out.append(Finding("error", "bad-level", f"registry.json {cid}",
                               f"c4_level '{comp.get('c4_level')}' must be one of {', '.join(C4_LEVELS)}."))
        if comp.get("status") not in CHANGE_STATUSES:
            out.append(Finding("error", "bad-status", f"registry.json {cid}",
                               f"status '{comp.get('status')}' must be one of {', '.join(CHANGE_STATUSES)}."))
        if comp.get("status") in CHANGING:
            change = comp.get("change") or {}
            waive = change.get("waive") or {}
            if not change.get("why"):
                out.append(Finding("error", "change-no-reason", f"registry.json {cid}",
                                   "component is changing but change.why is empty. Say why it changes."))
            if not change.get("ucs"):
                out.append(Finding("error", "change-no-uc", f"registry.json {cid}",
                                   "component change is linked to no UC. Add change.ucs."))
            for field, code in (("decisions", "change-no-decision"), ("files", "change-no-files")):
                if not change.get(field) and not waive.get(field):
                    out.append(Finding("error", code, f"registry.json {cid}",
                                       f"component change has no {field}. Add change.{field} or a reason in change.waive.{field}."))
            out += _unknown("registry.json", cid, change.get("ucs"), known["uc"], "UC")
            out += _unknown("registry.json", cid, change.get("decisions"), known["decision"], "decision")
    return out


def check_usecases(project):
    known, out = known_ids(project), check_ids("usecases.json", project.ucs, "uc")
    out += check_ids("usecases.json outcomes", project.usecases["outcomes"], "outcome")
    registry = {c.get("id"): c for c in project.components}
    outcome_ucs = {uc for o in project.usecases["outcomes"] for uc in o.get("ucs") or []}
    for uc in project.ucs:
        uid = uc.get("id", "?")
        where = f"usecases.json {uid}"
        if uc.get("tier") not in TIERS:
            out.append(Finding("error", "bad-tier", where, f"tier '{uc.get('tier')}' must be Main or Affected."))
        if not uc.get("components"):
            out.append(Finding("error", "orphan-uc", where, "UC is linked to no component. Add components[]."))
        out += _unknown("usecases.json", uid, uc.get("components"), known["component"], "component")
        if uc.get("tier") == "Affected":
            cause = registry.get(uc.get("caused_by"))
            if cause is None:
                out.append(Finding("error", "affected-no-cause", where,
                                   "Affected UC must name the changed component in caused_by (a registry ID)."))
            elif cause.get("status") not in CHANGING:
                out.append(Finding("error", "affected-bad-cause", where,
                                   f"caused_by {uc.get('caused_by')} is {cause.get('status')}. An Affected UC needs a changed, new or removed component."))
        elif uid not in outcome_ucs:
            out.append(Finding("warning", "uc-no-outcome", where, "Main UC is not listed under any outcome."))
        flows = uc.get("flows") or []
        for flow in flows:
            if flow.get("kind") not in FLOW_KINDS:
                out.append(Finding("error", "bad-flow-kind", where, f"flow kind '{flow.get('kind')}' must be good, bad or edge."))
        good = [f for f in flows if f.get("kind") == "good"]
        if not good:
            out.append(Finding("error", "no-flow", where, "UC has no good-path flow. Add flows[] with kind 'good'."))
        elif not all(f.get("diagram") for f in good):
            out.append(Finding("error", "no-diagram", where, "good-path flow has no sequence diagram file."))
    for outcome in project.usecases["outcomes"]:
        out += _unknown("usecases.json", outcome.get("id", "outcome"), outcome.get("ucs"), known["uc"], "UC")
    crud = project.usecases["crud"]
    if project.ucs and not crud and not project.usecases.get("crud_waiver"):
        out.append(Finding("error", "crud-missing", "usecases.json crud",
                           "no CRUD rows. List each entity with its create/read/update/delete UC, or explain in crud_waiver why none are stored."))
    for row in crud:
        if not row.get("entity"):
            out.append(Finding("error", "crud-no-entity", "usecases.json crud", "CRUD row has no entity name."))
        for op in CRUD_OPS:
            cell = (row.get(op) or "").strip()
            where = f"usecases.json crud {row.get('entity', '?')}.{op}"
            if re.fullmatch(ID_PATTERNS["uc"], cell):
                out += _unknown("usecases.json", where, [cell], known["uc"], "UC")
            elif not re.match(r"n/a\s*[:(]\s*\S", cell):
                out.append(Finding("error", "crud-gap", where,
                                   "cell has no UC and no 'n/a: reason'. Map it to a UC or say why it is not needed."))
    return out


def check_decisions(project):
    known, out = known_ids(project), check_ids("decisions.json", project.decision_list, "decision")
    for dec in project.decision_list:
        did = dec.get("id", "?")
        where = f"decisions.json {did}"
        if dec.get("status") not in ("open", "decided"):
            out.append(Finding("error", "bad-status", where, "status must be 'open' or 'decided'."))
        if not dec.get("ucs"):
            out.append(Finding("error", "decision-no-uc", where, "decision links to no UC. Add ucs[]."))
        out += _unknown("decisions.json", did, dec.get("ucs"), known["uc"], "UC")
        out += _unknown("decisions.json", did, dec.get("components"), known["component"], "component")
        options = dec.get("options") or []
        for opt in options:
            oid = f"{did}/{opt.get('id', '?')}"
            if opt.get("status") not in OPTION_STATUSES:
                out.append(Finding("error", "bad-option-status", f"decisions.json {oid}",
                                   f"status must be one of {', '.join(OPTION_STATUSES)}."))
            elif opt["status"] != "open" and not opt.get("reason"):
                out.append(Finding("error", "option-no-reason", f"decisions.json {oid}", "chosen/deferred/rejected option needs a reason."))
            if opt.get("status") == "deferred" and not opt.get("revisit_when"):
                out.append(Finding("error", "deferred-no-revisit", f"decisions.json {oid}",
                                   "deferred option must say what would bring it back (revisit_when)."))
            out += _unknown("decisions.json", oid, opt.get("ucs"), known["uc"], "UC")
        if dec.get("status") == "decided":
            if not any(o.get("status") == "chosen" for o in options):
                out.append(Finding("error", "decided-no-choice", where, "decided decision has no chosen option."))
            if not dec.get("date"):
                out.append(Finding("error", "decided-no-date", where, "decided decision needs a date."))
    return out


def check_concepts(project):
    known, out = known_ids(project), check_ids("concepts.json", project.concept_list, "concept")
    for concept in project.concept_list:
        cid = concept.get("id", "?")
        if not concept.get("diagram"):
            out.append(Finding("error", "concept-no-diagram", f"concepts.json {cid}", "every concept needs at least one diagram."))
        out += _unknown("concepts.json", cid, concept.get("ucs"), known["uc"], "UC")
        out += _unknown("concepts.json", cid, concept.get("decisions"), known["decision"], "decision")
        out += _unknown("concepts.json", cid, concept.get("components"), known["component"], "component")
    return out


def check_artifacts(project):
    return [Finding("error", "bad-url", f"artifacts.json tabs.{tab}",
                    f"url {entry.get('url')!r} must be an https URL on {', '.join(ARTIFACT_HOSTS)}. It will not be linked.")
            for tab, entry in project.artifacts["tabs"].items()
            if isinstance(entry, dict) and entry.get("url") and not safe_url(entry["url"])]


def diagram_refs(project):
    """[(where, relative_path)] for every diagram the pages embed."""
    refs = [("intent", INTENT_DIAGRAM)] if project.intent else []
    if project.decisions.get("tree_diagram"):
        refs.append(("decisions tree", project.decisions["tree_diagram"]))
    for dec in project.decision_list:
        refs += [(f"{dec.get('id')}/{o.get('id')}", o["wireframe"]) for o in dec.get("options", []) if o.get("wireframe")]
    refs += [(c.get("id"), c["diagram"]) for c in project.concept_list if c.get("diagram")]
    for uc in project.ucs:
        refs += [(f"{uc.get('id')} {f.get('kind')}", f["diagram"]) for f in uc.get("flows", []) if f.get("diagram")]
        refs += [(f"{uc.get('id')} detail", d["diagram"]) for d in uc.get("details", []) if d.get("diagram")]
    return refs


def check_diagrams(project):
    out = []
    for where, rel in diagram_refs(project):
        path, problem = resolve_diagram(project.root, rel)
        if problem:
            out.append(Finding("error", "bad-diagram-path", where, f"{rel} {problem}. Diagrams must be .svg files inside {project.root}."))
        elif not path.is_file():
            out.append(Finding("warning", "diagram-missing", where, f"{rel} not found in {project.root}. Produce it with diagram-design."))
    return out


def check_translations(project):
    out = [Finding("warning", f"th-{status}", f"i18n/th.json {key}",
                   "no Thai entry or fields missing. Run i18n-diff and translate." if status == "missing"
                   else "English changed since this Thai text was written. Re-translate.")
           for key, status, _ in th_status(project)]
    entries = content_entries(project)
    out += [Finding("warning", "th-orphan", f"i18n/th.json {key}", "entry has no matching ID in the data files. Remove it.")
            for key in project.th if key not in entries]
    return out


VALIDATION_STEPS = [
    ("registry.json", check_registry),
    ("usecases.json", check_usecases),
    ("decisions.json", check_decisions),
    ("concepts.json", check_concepts),
    ("artifacts.json", check_artifacts),
    ("diagrams/", check_diagrams),
    ("i18n/th.json", check_translations),
]


# --------------------------------------------------------------------------- git

def _git(root, *args):
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("git unavailable (%s)", exc)
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def git_updated(root):
    """(date, short_commit) of the last commit touching root, or None."""
    out = _git(root, "log", "-1", "--format=%cs|%h", "--", ".")
    return tuple(out.split("|")) if out else None


def git_log(root, ident=None, limit=20):
    """[(date, short_commit, subject)] for the whole doc, or commits whose message mentions ident."""
    args = ["log", f"-{limit}", "--format=%cs|%h|%s"]
    if ident:
        args += ["--fixed-strings", f"--grep={ident}"]
    out = _git(root, *args, "--", ".")
    return [tuple(line.split("|", 2)) for line in out.splitlines() if line]


# --------------------------------------------------------------------------- rendering

PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_.:]+)\}\}")
PART = re.compile(r"<!--part:(\w+)-->(.*?)<!--/part-->", re.DOTALL)
REF_TAB = [(r"UC-\d+", "tab4"), (r"D\d+", "tab2"), (r"C\d+", "tab3"), (r"CMP-.+", "tab5")]


def load_theme(name):
    """CSS text for a theme: a path to a .css file, or the name of a file in themes/."""
    path = Path(name)
    if not path.is_file():
        path = THEMES / f"{name}.css"
    if not path.is_file():
        available = ", ".join(sorted(p.stem for p in THEMES.glob("*.css")))
        raise DataError(f"theme '{name}' not found. Available: {available}. Or pass the path to a .css file.")
    return path.read_text(encoding="utf-8")


def md_lite(text):
    """Paragraphs and '- ' bullets to HTML. Input is escaped."""
    out, items = [], []

    def flush():
        if items:
            out.append("<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for raw in text.split("\n"):
        line = raw.strip()
        if line.startswith(("- ", "* ")):
            items.append(line[2:])
        else:
            flush()
            if line:
                out.append(f"<p>{esc(line)}</p>")
    flush()
    return "".join(out)


def split_bullets(text):
    """(prose_lines, bullet_lines) of a small Markdown block."""
    lines = text.split("\n")
    bullets = [line for line in lines if line.strip().startswith(("- ", "* "))]
    return "\n".join(line for line in lines if line not in bullets), "\n".join(bullets)


class Site:
    """Renders one project's pages. All HTML lives in templates/; this class only fills slots."""

    def __init__(self, project, print_mode=False, lang="en", theme=DEFAULT_THEME):
        self.p = project
        self.print_mode = print_mode
        self.lang = lang
        self.theme_css = load_theme(theme)
        self.ui_en = read_json(I18N / "en.json")
        self.ui_th = read_json(I18N / "th.json")
        try:
            self.entries = content_entries(project)
        except (TypeError, AttributeError) as exc:
            raise DataError(f"project data has an unexpected shape ({exc}). Run validate to find the field.") from exc
        self.reg = {c.get("id"): c for c in project.components}
        self.ucs = {u.get("id"): u for u in project.ucs}
        self.parts = {}
        self._load_parts("_parts.html")

    # --- templates
    def _load_parts(self, filename):
        self.parts.update({m.group(1): m.group(2).strip() for m in PART.finditer((TEMPLATES / filename).read_text(encoding="utf-8"))})

    def template(self, filename):
        """Template body without its <!--part--> blocks; the parts are registered for part()."""
        text = (TEMPLATES / filename).read_text(encoding="utf-8")
        self._load_parts(filename)
        return PART.sub("", text)

    def fill(self, template_text, /, **slots):
        def sub(match):
            name = match.group(1)
            if name.startswith("ui:"):
                return self.ui(name[3:])
            if name not in slots:
                raise DataError(f"template placeholder {{{{{name}}}}} has no value. Add it in artifact_sync.py or remove it from the template.")
            return str(slots[name])
        return PLACEHOLDER.sub(sub, template_text)

    def part(self, name, /, **slots):
        return self.fill(self.parts[name], **slots)

    # --- text in both languages
    def bi(self, en, th, block=False, fmt=esc):
        tag = "div" if block else "span"
        return f'<{tag} data-l="en">{fmt(en)}</{tag}><{tag} data-l="th">{fmt(th or en)}</{tag}>'

    def ui(self, key):
        if key not in self.ui_en:
            raise DataError(f"UI string '{key}' is missing from i18n/en.json. Add it (and its Thai in th.json).")
        return self.bi(self.ui_en[key], self.ui_th.get(key))

    def ui_plain(self, key):
        return f"{self.ui_en[key]} / {self.ui_th.get(key, self.ui_en[key])}"

    def ui_fmt(self, key, **values):
        return self.bi(self.ui_en[key].format(**values), self.ui_th.get(key, self.ui_en[key]).format(**values))

    def tx(self, key, path, block=False, fmt=esc):
        """Project content in both languages; empty string if the field is absent."""
        en = self.entries.get(key, {}).get(path)
        if en is None:
            return ""
        th = self.p.th.get(key, {}).get("text", {}).get(path)
        return self.bi(en, th, block=block, fmt=fmt)

    def tx_block(self, key, path):
        return self.tx(key, path, block=True, fmt=md_lite)

    # --- shared pieces
    def tab_url(self, tab):
        entry = self.p.artifacts["tabs"].get(tab)
        url = entry.get("url") if isinstance(entry, dict) else None
        return url if safe_url(url) else None

    def tab_title(self, tab):
        return self.ui_en[f"nav.{tab}"]

    def nav(self, current):
        items = []
        for tab in TABS:
            label = self.ui(f"nav.{tab}")
            if tab == current:
                items.append(self.part("nav_current", label=label))
            elif self.tab_url(tab):
                items.append(self.part("nav_link", href=esc(self.tab_url(tab), quote=True), label=label))
            else:
                items.append(self.part("nav_disabled", tooltip=esc(self.ui_plain("nav.disabled"), quote=True), label=label))
        return "".join(items)

    def ref(self, ident):
        tab = next((t for pattern, t in REF_TAB if re.fullmatch(pattern, ident)), None)
        url = self.tab_url(tab) if tab else None
        if url:
            return self.part("ref_link", href=esc(f"{url}#{ident}", quote=True), label=esc(ident))
        return self.part("ref_plain", label=esc(ident))

    def refs(self, idents):
        return ", ".join(self.ref(i) for i in idents or [])

    def comp_name(self, cid):
        return esc(self.reg.get(cid, {}).get("name", cid or "?"))

    def diagram(self, rel, alt=""):
        if not rel:
            return ""
        path, problem = resolve_diagram(self.p.root, rel)
        if problem or not path.is_file():
            return self.part("diagram_missing", file=esc(rel))
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return self.part("diagram", src=f"data:image/svg+xml;base64,{data}", alt=esc(alt))

    def changelog(self, ident=None, wrap=True):
        rows = git_log(self.p.root, ident)
        if rows:
            body = self.part("changelog_rows", rows="".join(
                self.part("changelog_row", date=esc(d), commit=esc(c), subject=esc(s)) for d, c, s in rows))
        else:
            body = self.part("changelog_empty")
        return self.part("changelog", rows=body, open_attr=self.open_attr()) if wrap else body

    def updated(self):
        info = git_updated(self.p.root)
        return esc(" · ".join(info)) if info else self.ui("footer.uncommitted")

    def before_after(self, heading_key):
        comps = self.p.components
        counts = {s: sum(1 for c in comps if c.get("status") == s) for s in CHANGE_STATUSES}

        def boxes(items, after):
            return "".join(self.part(
                "comp_box", status=esc(c.get("status", "unchanged")) if after else "unchanged", level=esc(c.get("c4_level", "")),
                id=esc(c.get("id", "")), name=esc(c.get("name", "")), hidden="" if self.print_mode or c.get("c4_level") == "L2" else "hidden",
                status_label=self.ui(f"status.{c['status']}") if after and c.get("status") not in (None, "unchanged") else "")
                for c in items)

        buttons = "".join(self.part("level_button", level=lv, pressed=str(lv == "L2").lower(), label=self.ui(f"level.{lv}")) for lv in C4_LEVELS)
        return self.part("before_after", heading=self.ui(heading_key), level_buttons=buttons,
                         before=boxes([c for c in comps if c.get("status") != "new"], False), after=boxes(comps, True),
                         summary=self.ui_fmt("ba.summary", **counts))

    def open_attr(self):
        return "open" if self.print_mode else ""

    # --- pages: each returns (slots, header_extra)
    def build_intent(self):
        preview = self.diagram(INTENT_DIAGRAM, "intent overview")
        slots = {key: self.tx_block("intent", key) for key in INTENT_SECTIONS}
        return dict(slots, preview=preview, changelog=self.changelog()), ""

    def build_tab1(self):
        recap = self.tx("intent", "before", True, lambda s: md_lite(split_bullets(s)[0]))
        issues = self.tx("intent", "before", True, lambda s: md_lite(split_bullets(s)[1]))
        affected = [u for u in self.p.ucs if u.get("tier") == "Affected"]

        def uc_line(uc, because=""):
            return self.part("uc_line", tier_class=esc(uc.get("tier", "").lower()), tier_label=self.ui(f"tier.{uc.get('tier', 'Main')}"),
                             id=esc(uc.get("id", "")), title=self.tx(uc.get("id"), "title"), because=because)

        outcomes = "".join(self.part("outcome", text=self.tx(o.get("id"), "text"), ucs="".join(
            uc_line(self.ucs[i]) for i in o.get("ucs", []) if i in self.ucs)) for o in self.p.usecases.get("outcomes", []))
        affected_html = "".join(uc_line(u, self.part("because", component=self.comp_name(u.get("caused_by")))) for u in affected)
        impact = "".join(self.part(
            "impact_row", component=self.comp_name(c.get("id")), level=esc(c.get("c4_level", "")), change=self.tx(c.get("id"), "what"),
            ucs=self.refs([u["id"] for u in affected if u.get("caused_by") == c.get("id")]) or self.ui("t1.none"))
            for c in self.p.components if c.get("status") in ("changed", "new", "removed"))
        crud = "".join(self.part("crud_row", entity=esc(r.get("entity", "")), cells="".join(self._crud_cell(r.get(op)) for op in CRUD_OPS))
                       for r in self.p.usecases.get("crud", []))
        slots = dict(recap=recap, issues=issues, outcomes=outcomes, affected=affected_html,
                     impact_rows=impact, crud_rows=crud, overview=self.before_after("t1.overview"))
        return slots, ""

    def _crud_cell(self, value):
        value = (value or "").strip()
        if re.fullmatch(r"UC-\d+", value):
            return self.part("crud_cell", cls="", value=self.ref(value))
        if value.startswith("n/a"):
            return self.part("crud_cell", cls="na", value=esc(value))
        return self.part("crud_cell", cls="gap", value=self.ui("crud.gap"))

    def build_tab2(self):
        decisions = self.p.decision_list
        is_open = [d for d in decisions if d.get("status") != "decided"]
        decided = [d for d in decisions if d.get("status") == "decided"]
        slots = dict(tree=self.diagram(self.p.decisions.get("tree_diagram"), "solution path tree"),
                     open="".join(self._decision(d) for d in is_open), decided="".join(self._decision(d) for d in decided))
        header = f'<span class="pill">{self.ui_fmt("t2.counts", open=len(is_open), decided=len(decided))}</span>'
        return slots, header

    def _decision(self, dec):
        did, decided = dec.get("id"), dec.get("status") == "decided"
        chosen = next((o for o in dec.get("options", []) if o.get("status") == "chosen"), None)
        chosen_line = self.part("chosen_line", title=self.tx(did, f"options.{dec['options'].index(chosen)}.title")) if decided and chosen else ""
        reason = self.tx(did, "choice_reason")
        return self.part(
            "decision", id=esc(did or ""), title=self.tx(did, "title"), state="chosen" if decided else "open",
            state_class="" if decided else "open-state", state_label=self.ui("state.decided" if decided else "state.open"),
            date=esc(dec.get("date", "")), chosen_line=chosen_line, ucs=self.refs(dec.get("ucs")),
            open_attr="" if decided and not self.print_mode else "open",
            options="".join(self._option(did, i, o) for i, o in enumerate(dec.get("options", []))),
            choice_reason=self.part("choice_reason", text=reason) if reason else "")

    def _option(self, did, index, opt):
        base = f"options.{index}"
        facts = [(f"opt.{k}", self.tx(did, f"{base}.{k}")) for k in ("pros", "cons", "cost", "risk")]
        facts.append(("opt.covers", self.refs(opt.get("ucs"))))
        if opt.get("status") == "deferred":
            facts.append(("opt.revisit", self.tx(did, f"{base}.revisit_when")))
        return self.part(
            "option", status=esc(opt.get("status", "open")), title=self.tx(did, f"{base}.title"),
            status_label=self.ui(f"status.{opt.get('status', 'open')}"), wireframe=self.diagram(opt.get("wireframe"), opt.get("id", "")),
            reason=self.tx(did, f"{base}.reason"),
            facts="".join(self.part("fact", label=self.ui(key), value=value) for key, value in facts if value))

    def build_tab3(self):
        concepts = self.p.concept_list
        chips = "".join(self.part("chip", id=esc(c.get("id", "")), title=self.tx(c.get("id"), "title")) for c in concepts)
        blocks = "".join(self.part(
            "concept", id=esc(c.get("id", "")), label=self.tx(c.get("id"), "label"), title=self.tx(c.get("id"), "title"),
            diagram=self.diagram(c.get("diagram"), c.get("id", "")), summary=self.tx_block(c.get("id"), "summary"),
            terms=self.part("terms", text=self.tx(c.get("id"), "terms")) if c.get("terms") else "",
            related=", ".join(filter(None, [self.refs(c.get("decisions")), self.refs(c.get("ucs")), self.refs(c.get("components"))])))
            for c in concepts)
        return dict(chips=chips, blocks=blocks), ""

    def build_tab4(self):
        ucs = self.p.ucs
        side = {tier: "".join(self.part("side_link", id=esc(u.get("id", "")), title=self.tx(u.get("id"), "title"))
                              for u in ucs if u.get("tier") == tier) for tier in TIERS}
        ordered = [u for tier in TIERS for u in ucs if u.get("tier") == tier]
        return dict(side_main=side["Main"], side_affected=side["Affected"], ucs="".join(self._uc(u) for u in ordered)), ""

    def _uc(self, uc):
        uid, flows = uc.get("id"), uc.get("flows", [])
        indexed = list(enumerate(flows))
        good = next((f for _, f in indexed if f.get("kind") == "good"), {})
        alts = "".join(self.part(
            "alt", kind=esc(f["kind"]), group=esc(f"{uid}-{f['kind']}"), hidden="" if self.print_mode else "hidden",
            label=self.ui(f"t4.{f['kind']}_one"), title=self.tx(uid, f"flows.{i}.title"), diagram=self.diagram(f.get("diagram"), f.get("title", "")))
            for i, f in indexed if f.get("kind") in ("bad", "edge"))
        toggles = "".join(self.part("toggle", kind=kind, group=esc(f"{uid}-{kind}"), expanded=str(self.print_mode).lower(), label=self.ui(f"t4.{kind}"))
                          for kind in ("bad", "edge") if any(f.get("kind") == kind for f in flows))
        rows = "".join(self.part("touchpoint_row", scenario=self.ui(f"t4.{f['kind']}") if f["kind"] != "good" else self.ui("t4.good"),
                                 step=esc(str(t.get("step", ""))), sees=self.tx(uid, f"flows.{i}.touchpoints.{j}.sees"))
                       for i, f in indexed for j, t in enumerate(f.get("touchpoints", [])))
        details = "".join(self.part("detail", title=self.tx(uid, f"details.{i}.title"), diagram=self.diagram(d.get("diagram")), open_attr=self.open_attr())
                          for i, d in enumerate(uc.get("details", [])))
        return self.part(
            "uc", id=esc(uid or ""), title=self.tx(uid, "title"), tier=self.ui(f"tier.{uc.get('tier', 'Main')}"),
            source=self.ui(f"source.{uc.get('source', 'code')}"), summary=self.tx(uid, "summary"), toggles=toggles,
            good=self.diagram(good.get("diagram"), good.get("title", "")), alts=alts, touchpoints=rows, details=details,
            open_attr=self.open_attr(), track=self.changelog(uid, wrap=False))

    def build_tab5(self):
        cards = "".join(self._component(c) for c in self.p.components if c.get("status") != "unchanged")
        return dict(overview=self.before_after("t5.overview"), components=cards, changelog=self.changelog()), ""

    def _component(self, comp):
        cid, change = comp.get("id"), comp.get("change", {})
        status = comp.get("status", "unchanged")
        data = "".join(self.part("row3", a=esc(r.get("object", "")), b=esc(r.get("change", "")), c=esc(r.get("migration", ""))) for r in change.get("data", []))
        infra = "".join(self.part("row3", a=esc(r.get("item", "")), b=esc(str(r.get("before", ""))), c=esc(str(r.get("after", "")))) for r in change.get("infra", []))
        files = change.get("files", [])
        return self.part(
            "component", id=esc(cid or ""), status=esc(status), name=esc(comp.get("name", "")), level=esc(comp.get("c4_level", "")),
            open_attr="open" if status == "changed" or self.print_mode else "", badge=self.part("badge", status=esc(status), label=self.ui(f"status.{status}")),
            what=self.tx_block(cid, "what"), why=self.tx_block(cid, "why"), links=", ".join(filter(None, [self.refs(change.get("decisions")), self.refs(change.get("ucs"))])),
            data=self.part("data_table", rows=data) if data else "", infra=self.part("infra_table", rows=infra) if infra else "",
            files=self.part("files", open_attr=self.open_attr(), count=len(files), items="".join(self.part("file_item", path=esc(f)) for f in files)) if files else "")

    # --- page assembly
    def content(self, tab):
        """(page_title, header_extra, content_html) for one tab."""
        template = self.template(f"{tab}.html")  # registers the tab's parts before the build_* methods use them
        slots, header_extra = getattr(self, f"build_{tab}")()
        content = self.fill(template, **slots)
        return f"{self.p.name} · {self.tab_title(tab)}", header_extra, content

    def page(self, tab):
        title, header_extra, content = self.content(tab)
        label = self.ui("page.intent" if tab == "intent" else "page.solution")
        return self.fill(
            self.template("_base.html"), lang=self.lang, title=esc(title), theme_css=self.theme_css,
            base_css=(TEMPLATES / "base.css").read_text(encoding="utf-8"), base_js=(TEMPLATES / "base.js").read_text(encoding="utf-8"),
            body_class="", project=esc(self.p.name), page_label=label, header_extra=header_extra, nav=self.nav(tab),
            content=content, updated=self.updated())

    def print_document(self, tabs):
        template = self.template("print.html")
        rendered = [self.content(t) for t in tabs]
        pages = "".join(self.part("print_page", title=esc(title), content=content) for title, _, content in rendered)
        toc = "".join(self.part("toc_item", label=self.ui(f"nav.{t}")) for t in tabs)
        return self.fill(template, lang=self.lang, title=esc(f"{self.p.name} · export"), project=esc(self.p.name),
                         theme_css=self.theme_css, base_css=(TEMPLATES / "base.css").read_text(encoding="utf-8"),
                         updated=self.updated(), toc=toc, pages=pages)


# --------------------------------------------------------------------------- commands

@dataclass
class Summary:
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0

    def report(self, command):
        total = self.succeeded + self.failed + self.skipped
        log.info("Summary [%s] %s: processed=%d succeeded=%d failed=%d skipped=%d",
                 command, datetime.now().isoformat(timespec="seconds"), total, self.succeeded, self.failed, self.skipped)


def cmd_validate(args):
    project, summary, findings = load_project(args.artifact_dir), Summary(), []
    for label, check in VALIDATION_STEPS:
        if label.endswith(".json") and label != "i18n/th.json" and not (project.root / label).is_file():
            log.info("skip   %s (not present)", label)
            summary.skipped += 1
            continue
        try:
            found = check(project)
        except (TypeError, AttributeError, KeyError) as exc:
            found = [Finding("error", "bad-shape", label, f"unexpected data shape ({type(exc).__name__}: {exc}). "
                             "Lists must be lists and objects must be objects.")]
        findings += found
        errors = sum(1 for f in found if f.severity == "error")
        log.info("%s %s: %d errors, %d warnings", "FAIL " if errors else "ok   ", label, errors, len(found) - errors)
        summary.failed, summary.succeeded = summary.failed + bool(errors), summary.succeeded + (not errors)
    for finding in findings:
        print(finding)
    summary.report("validate")
    bad = [f for f in findings if f.severity == "error" or args.strict]
    return 1 if bad else 0


def cmd_render(args):
    project, summary = load_project(args.artifact_dir), Summary()
    available = present_tabs(project.root)
    site, out_dir = Site(project, theme=args.theme), project.root / "build"
    for tab in args.tabs or TABS:
        if tab not in available:
            log.info("skip   %s (needs %s)", tab, TAB_DATA_FILES[tab])
            summary.skipped += 1
            continue
        try:
            page = site.page(tab)
        except (DataError, KeyError, ValueError, TypeError, AttributeError) as exc:
            log.error("FAIL   %s: %s", tab, exc)
            summary.failed += 1
            continue
        target = out_dir / f"{tab}.html"
        if args.dry_run:
            log.info("would write %s (%d bytes)", target, len(page.encode("utf-8")))
        else:
            out_dir.mkdir(exist_ok=True)
            target.write_text(page, encoding="utf-8")
            log.info("wrote  %s (%d bytes)", target, len(page.encode("utf-8")))
        summary.succeeded += 1
    summary.report("render")
    return 1 if summary.failed else 0


def find_browser():
    candidates = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]
    found = [c for c in candidates if Path(c).is_file()]
    return found[0] if found else next((w for w in map(shutil.which, ("msedge", "chrome", "google-chrome", "chromium")) if w), None)


def cmd_export_pdf(args):
    project, summary = load_project(args.artifact_dir), Summary()
    tabs = present_tabs(project.root)
    out_dir = project.root.resolve() / "build"
    html_path, pdf_path = out_dir / "artifact-print.html", out_dir / "artifact.pdf"
    site = Site(project, print_mode=True, lang=args.lang, theme=args.theme)  # bad theme/shape exits via main()
    try:
        document = site.print_document(tabs)
    except (DataError, KeyError, ValueError, TypeError, AttributeError) as exc:
        log.error("FAIL   print HTML: %s", exc)
        summary.failed += 1
        summary.report("export-pdf")
        return 1
    log.info("combined print HTML: cover + %s", ", ".join(tabs))
    if args.dry_run:
        log.info("would write %s (%d bytes) and convert to %s", html_path, len(document.encode("utf-8")), pdf_path)
        summary.succeeded += 1
        summary.report("export-pdf")
        return 0
    out_dir.mkdir(exist_ok=True)
    html_path.write_text(document, encoding="utf-8")
    log.info("wrote  %s", html_path)
    summary.succeeded += 1
    browser = find_browser()
    if not browser:
        log.warning("no Edge/Chrome found. Open %s in a browser and print to PDF (enable background graphics).", html_path)
        summary.skipped += 1
    else:
        done = subprocess.run([browser, "--headless", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
                              capture_output=True, text=True, timeout=120)
        if pdf_path.is_file():
            log.info("wrote  %s", pdf_path)
            summary.succeeded += 1
        else:
            log.error("PDF conversion failed (exit %s): %s. Open %s and print to PDF instead.", done.returncode, done.stderr.strip()[-200:], html_path)
            summary.failed += 1
    summary.report("export-pdf")
    return 1 if summary.failed else 0


def cmd_i18n_diff(args):
    project = load_project(args.artifact_dir)
    todo = [{"key": key, "status": status, "source_hash": source_hash(text), "text": text} for key, status, text in th_status(project)]
    for item in todo:
        log.info("%-8s %s (%d fields)", item["status"], item["key"], len(item["text"]))
    log.info("%d entries need Thai. Write them to i18n/th.json as {key: {source_hash, text}}.", len(todo))
    print(json.dumps(todo, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler, helptext in [("validate", cmd_validate, "traceability check"), ("render", cmd_render, "render HTML pages"),
                                    ("export-pdf", cmd_export_pdf, "combined print HTML and PDF"), ("i18n-diff", cmd_i18n_diff, "list missing or stale Thai")]:
        cmd = sub.add_parser(name, help=helptext)
        cmd.add_argument("artifact_dir", help="the project's docs/artifact folder")
        cmd.set_defaults(handler=handler)
        if name == "validate":
            cmd.add_argument("--strict", action="store_true", help="treat warnings as errors")
        if name == "render":
            cmd.add_argument("--tabs", nargs="+", choices=TABS, help="pages to render (default: all present)")
        if name in ("render", "export-pdf"):
            cmd.add_argument("--theme", default=DEFAULT_THEME, help="theme name in themes/ or path to a .css file (default anthropic)")
            cmd.add_argument("--dry-run", action="store_true", help="log what would be written; write nothing")
        if name == "export-pdf":
            cmd.add_argument("--lang", choices=("en", "th"), default="en", help="language of the PDF (default en)")
    return parser


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except DataError as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
