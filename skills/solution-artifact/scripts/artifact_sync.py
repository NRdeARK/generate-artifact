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
import hashlib
import html
import json
import logging
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
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
OVERVIEW_LEVELS = ("L1", "L2")  # L3 = components inside ONE container, so it never joins the system-wide switch
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
             "sees", "terms", "label", "choice_reason", "revisit_when", "text", "meaning",
             "plain", "before", "now", "analogy"}
# data, not prose: never translated even when a key name matches
NON_TRANSLATABLE = {"infra", "example", "samples", "sample_rows", "data", "files", "default", "steps"}

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
            if key in NON_TRANSLATABLE:
                continue
            if key == "diagrams" and isinstance(value, dict):
                value = {"flowchart": value.get("flowchart") or []}  # only flowchart titles are prose; the rest are file paths
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
            if isinstance(item.get("id"), str) and text:
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
# file change type -> (theme status class, marker readable without color)
FILE_CHANGES = {"added": ("new", "+"), "modified": ("changed", "~"), "removed": ("removed", "-"), "renamed": ("changed", "\u2192")}
STORE_KINDS = ("db", "store")
ARTIFACT_AUTHORITY = "claude.ai"


def safe_url(url):
    """True only for https://claude.ai/... URLs. Rejects anything that parsers could read differently:
    backslashes, control characters, surrounding spaces, credentials, ports, other hosts. Never raises."""
    if not isinstance(url, str) or url != url.strip() or "\\" in url or any(ord(c) < 32 or ord(c) == 127 for c in url):
        return False
    try:
        parts = urlparse(url)
        parts.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError:
        return False
    return parts.scheme == "https" and parts.netloc == ARTIFACT_AUTHORITY


def reason_text(value):
    """A waiver reason must be a non-blank string (not true, a list, or spaces)."""
    return isinstance(value, str) and bool(value.strip())


SVG_NS = "http://www.w3.org/2000/svg"
SVG_TAGS = {"svg", "g", "defs", "title", "desc", "rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text", "tspan", "marker"}
ET.register_namespace("", SVG_NS)


def sanitize_svg(text):
    """(clean_svg, problems). Keeps drawing elements only: strips scripts, foreignObject, style elements,
    event handlers and external references. An empty clean_svg means the file is unusable."""
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
        return "", ["DOCTYPE or ENTITY declarations are not allowed"]
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return "", [f"not valid XML ({exc})"]
    if root.tag != f"{{{SVG_NS}}}svg":
        return "", ["the root element is not <svg>"]
    problems = []

    def clean(element):
        for child in list(element):
            name = child.tag.split("}")[-1] if isinstance(child.tag, str) else ""
            if name not in SVG_TAGS:
                element.remove(child)
                problems.append(f"element <{name or 'unknown'}> is not allowed")
                continue
            clean(child)
        for attr in list(element.attrib):
            local, value = attr.split("}")[-1].lower(), element.attrib[attr]
            external = re.search(r"url\(\s*['\"]?(?!#)", value) or (local == "href" and not value.startswith("#"))
            if local.startswith("on") or "javascript:" in value.lower() or external:
                del element.attrib[attr]
                problems.append(f"attribute {local} is not allowed")

    clean(root)
    return ET.tostring(root, encoding="unicode"), problems


# A UC sequence diagram marks each message group with data-step="<step label>". usecases.json step_changes maps a step label to
# "added" or "changed" (default unchanged); the renderer recolours those arrows and adds a text marker, so colour is never the only cue.
STEP_CHANGE_VALUES = ("added", "changed", "unchanged")
STEP_STYLE = {"added": ("#1F6FA8", "arrow-new", "#CFE4F5", "#0B3D63", "+", "Added in this use case"),
              "changed": ("#B87800", "arrow-changed", "#FBE3B0", "#5C3A00", "~", "Changed in this use case")}


def mark_step_changes(clean_svg, changes):
    """(svg, steps_found): recolour the arrows of added/changed steps and tag their labels. Elements and attributes stay within the sanitizer allow-list."""
    root = ET.fromstring(clean_svg)
    svg_id = root.get("id", "")
    markers = {m.get("id") for m in root.iter(f"{{{SVG_NS}}}marker")}
    q = lambda name: f"{{{SVG_NS}}}{name}"  # noqa: E731
    found = set()
    for group in root.iter(q("g")):
        step = group.get("data-step")
        if step is None:
            continue
        found.add(step)
        kind = changes.get(step)
        if kind not in STEP_STYLE:
            continue
        stroke, marker, tint, ink, sign, tip = STEP_STYLE[kind]
        group.set("data-change", kind)
        for el in group:
            if el.tag in (q("line"), q("path")) and el.get("marker-end"):
                el.set("stroke", stroke)
                el.set("stroke-width", "2.4")
                if f"{svg_id}-{marker}" in markers:
                    el.set("marker-end", f"url(#{svg_id}-{marker})")
        mask = next((el for el in group if el.tag == q("rect") and el.get("height") == "16"), None)
        if mask is None:
            continue
        x, y = float(mask.get("x")) - 22, float(mask.get("y"))
        tag = ET.SubElement(group, q("rect"), x=f"{x:g}", y=f"{y:g}", width="16", height="16", rx="2", fill=tint, stroke=stroke)
        ET.SubElement(tag, q("title")).text = tip
        text = ET.SubElement(group, q("text"), x=f"{x + 8:g}", y=f"{y + 12:g}", fill=ink, **{
            "font-size": "12", "font-weight": "700", "text-anchor": "middle", "font-family": "ui-monospace, 'Cascadia Mono', Consolas, monospace"})
        text.text = sign
    return ET.tostring(root, encoding="unicode"), found


def read_diagram(root, rel):
    """(clean_svg, problems) for a diagram path, or (None, [reason]) when the path or file is unusable."""
    path, problem = resolve_diagram(root, rel)
    if problem:
        return None, [f"{rel} {problem}"]
    if not path.is_file():
        return None, [f"{rel} not found"]
    return sanitize_svg(path.read_text(encoding="utf-8"))


def svg_width(clean_svg):
    match = re.search(r'viewBox="[\d.]+ [\d.]+ ([\d.]+) [\d.]+"', clean_svg)
    return int(float(match.group(1))) if match else 0


def resolve_diagram(root, rel):
    """(path, problem). problem is set unless rel is an .svg file path inside root. Used by validator and renderer."""
    path = Path(root) / rel
    if path.suffix.lower() != ".svg":
        return None, "is not an .svg file"
    if not path.resolve().is_relative_to(Path(root).resolve()):
        return None, "points outside the project folder"
    return path, None

def file_entries(change):
    """Normalize change.files: plain strings count as 'modified' (legacy), objects keep path/change/from."""
    entries = []
    for item in (change or {}).get("files") or []:
        if isinstance(item, str):
            entries.append({"path": item, "change": "modified", "legacy": True})
        elif isinstance(item, dict):
            entries.append({"path": item.get("path", ""), "change": item.get("change"), "from": item.get("from"), "legacy": False})
    return entries


def string_ids(items):
    """The IDs that are strings. Other IDs are reported by check_ids and never used as keys."""
    return {i["id"]: i for i in items if isinstance(i.get("id"), str)}


def _unknown(file, where, refs, known, kind):
    return [Finding("error", "unknown-id", f"{file} {where}",
                    f"{kind} '{ref}' is not defined. Add it to its source file or fix the ID.")
            for ref in refs or [] if not isinstance(ref, str) or ref not in known]


def check_ids(file, items, kind):
    """Every item needs an ID of the right shape, unique within its collection."""
    out, seen = [], set()
    for index, item in enumerate(items, start=1):
        ident, where = item.get("id"), f"{file} item {index}"
        if ident in (None, ""):
            out.append(Finding("error", "missing-id", where, f"{kind} has no id. Give it a stable ID."))
        elif not isinstance(ident, str) or not re.fullmatch(ID_PATTERNS[kind], ident):
            out.append(Finding("error", "bad-id", where, f"{kind} id {ident!r} must match {ID_PATTERNS[kind]}."))
        elif ident in seen:
            out.append(Finding("error", "duplicate-id", f"{file} {ident}", "ID used twice. IDs must be unique."))
        if isinstance(ident, str):
            seen.add(ident)
    return out


def known_ids(project):
    return {
        "component": set(string_ids(project.components)),
        "uc": set(string_ids(project.ucs)),
        "decision": set(string_ids(project.decision_list)),
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
            waive = change.get("waive") if isinstance(change.get("waive"), dict) else {}
            if not change.get("why"):
                out.append(Finding("error", "change-no-reason", f"registry.json {cid}",
                                   "component is changing but change.why is empty. Say why it changes."))
            if not change.get("ucs"):
                out.append(Finding("error", "change-no-uc", f"registry.json {cid}",
                                   "component change is linked to no UC. Add change.ucs."))
            for field, code in (("decisions", "change-no-decision"), ("files", "change-no-files")):
                if not change.get(field) and not reason_text(waive.get(field)):
                    out.append(Finding("error", code, f"registry.json {cid}",
                                       f"component change has no {field}. Add change.{field} or a non-blank text reason in change.waive.{field}."))
            out += _unknown("registry.json", cid, change.get("ucs"), known["uc"], "UC")
            out += _unknown("registry.json", cid, change.get("decisions"), known["decision"], "decision")
            for table in (change.get("data_dictionary") or {}).get("tables") or []:
                for column in table.get("columns") or []:
                    if column.get("change") in ("new", "changed") and not column.get("meaning"):
                        out.append(Finding("warning", "column-meaning-missing", f"registry.json {cid} {table.get('table')}.{column.get('name')}",
                                           "a new or changed column needs a meaning in plain words (and allowed values for an enum)."))
            files = file_entries(change)
            if any(f["legacy"] for f in files):
                out.append(Finding("warning", "file-change-type-missing", f"registry.json {cid}",
                                   f"{sum(f['legacy'] for f in files)} file(s) are plain strings. Use {{path, change: added|modified|removed|renamed}}."))
            for f in files:
                if not f["legacy"] and f["change"] not in FILE_CHANGES:
                    out.append(Finding("error", "bad-file-change", f"registry.json {cid} {f['path']}",
                                       f"change {f['change']!r} must be one of {', '.join(FILE_CHANGES)}."))
                elif f["change"] == "renamed" and not f["from"]:
                    out.append(Finding("error", "file-rename-no-from", f"registry.json {cid} {f['path']}", "a renamed file needs from (its old path)."))
    views = project.registry.get("views") if isinstance(project.registry.get("views"), dict) else {}
    if "L3" in views:
        out.append(Finding("error", "l3-not-global", "registry.json views.L3",
                           "C4 L3 shows components inside ONE container. Move it to that container's own views.L3 (e.g. on CMP-API)."))
    for level in overview_levels(project):
        entry = views.get(level)
        if not (isinstance(entry, dict) and entry.get("before") and entry.get("after")):
            out.append(Finding("warning", "view-missing", f"registry.json views.{level}",
                               f"level {level} has components but no before/after data-flow diagram. Add views.{level}.before and .after (diagrams/arch-{level}-before.svg)."))
    for comp in project.components:
        cid = comp.get("id", "?")
        if comp.get("c4_level") == "L3":
            parent = string_ids(project.components).get(comp.get("parent")) if isinstance(comp.get("parent"), str) else None
            if comp.get("parent") in (None, ""):
                out.append(Finding("error", "l3-no-parent", f"registry.json {cid}", "an L3 component must name its container in parent (e.g. CMP-API)."))
            elif parent is None or parent.get("c4_level") != "L2":
                out.append(Finding("error", "l3-bad-parent", f"registry.json {cid}", f"parent {comp.get('parent')!r} must be an L2 container in the registry."))
        paths = l3_view_paths(project, comp)
        declared = comp.get("views", {}).get("L3") if isinstance(comp.get("views"), dict) else None
        if paths and l3_children(project, cid) and not (isinstance(declared, dict) and declared.get("before") and declared.get("after")):
            out.append(Finding("warning", "view-missing", f"registry.json {cid} views.L3",
                               f"{cid} has L3 components but no views.L3 before/after diagram of what is inside it."))
        if comp.get("status") in CHANGING:
            diagrams = (comp.get("change") or {}).get("diagrams") or {}
            for key in ("before", "after"):
                if not diagrams.get(key):
                    out.append(Finding("warning", "component-view-missing", f"registry.json {cid}",
                                       f"changing component has no change.diagrams.{key}. Add a focused {key} diagram."))
            if comp.get("kind") in ("db", "store", "queue") and not diagrams.get("extra"):
                out.append(Finding("warning", "component-extra-missing", f"registry.json {cid}",
                                   f"a {comp.get('kind')} needs change.diagrams.extra (schema diff for a db/store, topology for a queue)."))
            for fc in diagrams.get("flowchart") or []:
                if not (fc.get("title") and fc.get("diagram")):
                    out.append(Finding("error", "flowchart-incomplete", f"registry.json {cid}", "each flowchart needs a title and a diagram."))
    return out


def required_entities(project):
    """Entities the CRUD matrix must cover: stored components (L2 db/store) plus every entity a UC names."""
    names = [c.get("name") for c in project.components if c.get("c4_level") == "L2" and c.get("kind") in STORE_KINDS]
    for uc in project.ucs:
        names += uc.get("entities") or []
    return list(dict.fromkeys(n for n in names if isinstance(n, str) and n.strip()))


def check_usecases(project):
    known, out = known_ids(project), check_ids("usecases.json", project.ucs, "uc")
    out += check_ids("usecases.json outcomes", project.usecases["outcomes"], "outcome")
    registry = string_ids(project.components)
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
            cause = registry.get(uc.get("caused_by")) if isinstance(uc.get("caused_by"), str) else None
            if cause is None:
                out.append(Finding("error", "affected-no-cause", where,
                                   "Affected UC must name the changed component in caused_by (a registry ID)."))
            elif cause.get("status") not in CHANGING:
                out.append(Finding("error", "affected-bad-cause", where,
                                   f"caused_by {uc.get('caused_by')} is {cause.get('status')}. An Affected UC needs a changed, new or removed component."))
        elif isinstance(uid, str) and uid not in outcome_ucs:
            out.append(Finding("warning", "uc-no-outcome", where, "Main UC is not listed under any outcome."))
        flows = uc.get("flows") or []
        for flow in flows:
            if flow.get("kind") not in FLOW_KINDS:
                out.append(Finding("error", "bad-flow-kind", where, f"flow kind '{flow.get('kind')}' must be good, bad or edge."))
        if not any(f.get("kind") == "good" for f in flows):
            out.append(Finding("error", "no-flow", where, "UC has no good-path flow. Add flows[] with kind 'good'."))
        if not uc.get("diagram"):
            out.append(Finding("error", "no-diagram", where, "UC has no sequence diagram. Add diagram: one SVG with the good path and every bad/edge flow as an alt fragment."))
        flow_ids = [f.get("id") for f in flows if isinstance(f.get("id"), str)]
        out += [Finding("error", "duplicate-id", f"{where} flows", f"flow id {i!r} used twice.") for i in sorted({i for i in flow_ids if flow_ids.count(i) > 1})]
    for outcome in project.usecases["outcomes"]:
        out += _unknown("usecases.json", outcome.get("id", "outcome"), outcome.get("ucs"), known["uc"], "UC")
    crud = project.usecases["crud"]
    waived = reason_text(project.usecases.get("crud_waiver"))
    if project.ucs and not crud and not waived:
        out.append(Finding("error", "crud-missing", "usecases.json crud",
                           "no CRUD rows. List each entity with its create/read/update/delete UC, or give a non-blank text reason in crud_waiver."))
    if not waived:
        rows = {str(r.get("entity", "")).strip().lower() for r in crud}
        out += [Finding("error", "crud-missing-entity", f"usecases.json crud {name}",
                        "no CRUD row for this entity. Add a row, or explain in crud_waiver why none are stored.")
                for name in required_entities(project) if name.strip().lower() not in rows]
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


def check_step_changes(project):
    out = []
    for uc in project.ucs:
        changes = uc.get("step_changes")
        where = f"usecases.json {uc.get('id')} step_changes"
        if changes is None:
            continue
        if not isinstance(changes, dict):
            out.append(Finding("error", "step-change-invalid", where, "step_changes must be an object like {\"2\": \"added\", \"3b\": \"changed\"}."))
            continue
        out += [Finding("error", "step-change-invalid", f"{where}.{step}",
                        f"'{value}' is not allowed. Use one of: {', '.join(STEP_CHANGE_VALUES)} (unchanged is the default).")
                for step, value in changes.items() if value not in STEP_CHANGE_VALUES]
    return out


# Big-project rules for sequence diagrams. usecases.json UC field `steps` (optional) lists the arrows:
# [{"id": "2", "label": "[REST] Fetch latest status", "from": "CMP-WEB", "to": "CMP-API", "call": "web/src/api.ts:12", "flow": "good"}]
MAX_STEPS, MAX_PARTICIPANTS = 12, 6
INFRA_KINDS = ("db", "store", "queue", "cache", "external")
LABEL_TAGS = ("REST", "gRPC", "SQL", "MQ", "CACHE", "WS", "CALL")
CODE_SOURCES = ("code", "mixed")
TAG_RE = re.compile(r"^\[(%s)\]\s*\S" % "|".join(re.escape(t) for t in LABEL_TAGS))
DETAIL_RES = (re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\b"),
              re.compile(r"(?<![\w.])/[A-Za-z0-9_-]+(?:/[\w{}:.-]+)*"),
              re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b.*\b(FROM|INTO|SET)\b", re.I))
CALL_RE = re.compile(r"^(.+):(\d+)$")


def uc_steps(uc):
    """The UC's step objects (dicts only); [] when absent or malformed."""
    steps = uc.get("steps")
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []


def check_uc_size(uc):
    uid, out = uc.get("id", "?"), []
    by_flow = {}
    for step in uc_steps(uc):
        flow = step.get("flow")
        by_flow.setdefault(flow if isinstance(flow, str) and flow else "good", []).append(step)
    for flow, steps in by_flow.items():
        if len(steps) > MAX_STEPS:
            out.append(Finding("warning", "uc-too-large", f"usecases.json {uid} flow {flow}",
                               f"{len(steps)} steps (max {MAX_STEPS}). Split it into smaller UCs, then redraw each diagram."))
    parts = uc.get("components") if isinstance(uc.get("components"), list) else []
    if len(parts) > MAX_PARTICIPANTS:
        out.append(Finding("warning", "uc-too-large", f"usecases.json {uid}",
                           f"{len(parts)} participants (max {MAX_PARTICIPANTS}). Split it into smaller UCs, then redraw each diagram."))
    return out


def check_infra_participants(uc, registry, diagram_text=None):
    """Steps that talk to a db/store/queue/cache/external component must keep it in the UC's participants and diagram."""
    uid, out, seen = uc.get("id", "?"), [], set()
    listed = {c for c in (uc.get("components") if isinstance(uc.get("components"), list) else []) if isinstance(c, str)}
    for step in uc_steps(uc):
        for end in (step.get("from"), step.get("to")):
            comp = registry.get(end) if isinstance(end, str) else None
            if not comp or comp.get("kind") not in INFRA_KINDS or end in seen:
                continue
            in_diagram = diagram_text is None or str(comp.get("name", "")) in diagram_text
            if end not in listed or not in_diagram:
                seen.add(end)
                out.append(Finding("warning", "infra-participant-missing", f"usecases.json {uid} step {step.get('id', '?')}",
                                   f"step talks to {end} ({comp.get('kind')}) but it is not a participant in the UC components and diagram. "
                                   "Add it to components[] and draw it as a lifeline; infra stays in sequence diagrams."))
    return out


def check_step_labels(uc):
    out = []
    for step in uc_steps(uc):
        label, where = step.get("label"), f"usecases.json {uc.get('id', '?')} step {step.get('id', '?')}"
        label = label if isinstance(label, str) else ""
        if not TAG_RE.match(label):
            out.append(Finding("warning", "label-format", where,
                               f"label '{label}' must start with one tag of {', '.join('[%s]' % t for t in LABEL_TAGS)} then a short action, e.g. '[REST] Fetch latest status'."))
        if any(rx.search(label) for rx in DETAIL_RES):
            out.append(Finding("warning", "label-too-detailed", where,
                               f"label '{label}' has an HTTP method, URL path or SQL. Say what the step does ('[REST] Fetch latest status'); put the detail in the UC details diagram."))
    return out


def check_call_sites(uc, repo_root):
    """Each step of a code/mixed UC needs call 'path:line' pointing at a real source line. Draft UCs are exempt; repo_root None skips existence."""
    if uc.get("source") not in CODE_SOURCES:
        return []
    out = []
    for step in uc_steps(uc):
        where, call = f"usecases.json {uc.get('id', '?')} step {step.get('id', '?')}", step.get("call")
        match = CALL_RE.match(call) if isinstance(call, str) else None
        if not match:
            out.append(Finding("warning", "arrow-no-callsite", where,
                               f"{uc.get('source')} UC needs call: \"path:line\" (the code that makes this arrow). Add it, or mark the UC source 'draft' if the code is not written yet."))
            continue
        rel, line = match.group(1), int(match.group(2))
        if rel.lower().endswith(".md"):
            out.append(Finding("warning", "callsite-missing", where, f"call {call} points at a doc. Docs are not a valid source; point at code."))
        elif repo_root is not None:
            try:
                target = (repo_root / rel).resolve()
                inside = repo_root.resolve() in target.parents
                exists = inside and target.is_file()
            except (ValueError, OSError):
                exists = False  # e.g. NUL byte or over-long path in call
            if not exists:
                out.append(Finding("warning", "callsite-missing", where, f"call {call}: {rel} does not exist under {repo_root}. Fix the path."))
            elif line < 1 or line > len(target.read_text(encoding="utf-8", errors="replace").splitlines()):
                out.append(Finding("warning", "callsite-missing", where, f"call {call}: {rel} has fewer than {line} lines. Fix the line number."))
    return out


def repo_root_of(artifact_dir):
    """Parent of docs/artifact, or None when the folder is not laid out that way."""
    root = Path(artifact_dir).resolve()
    return root.parent.parent if root.parent.name == "docs" else None


def check_big_project(project):
    registry, repo_root, out = string_ids(project.components), repo_root_of(project.root), []
    for uc in project.ucs:
        diagram_text = None
        if uc.get("diagram"):
            clean, _ = read_diagram(project.root, uc["diagram"])
            diagram_text = html.unescape(re.sub(r"<[^>]+>", "\u0000", clean)) if clean else None
        out += (check_uc_size(uc) + check_infra_participants(uc, registry, diagram_text)
                + check_step_labels(uc) + check_call_sites(uc, repo_root))
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
        if not concept.get("plain"):
            out.append(Finding("warning", "concept-plain-missing", f"concepts.json {cid}", "add plain: one sentence a non-engineer understands (plus before, now, why, optional analogy)."))
        out += _unknown("concepts.json", cid, concept.get("ucs"), known["uc"], "UC")
        out += _unknown("concepts.json", cid, concept.get("decisions"), known["decision"], "decision")
        out += _unknown("concepts.json", cid, concept.get("components"), known["component"], "component")
    return out


def check_artifacts(project):
    return [Finding("error", "bad-url", f"artifacts.json tabs.{tab}",
                    f"url {entry.get('url')!r} must be https://{ARTIFACT_AUTHORITY}/... exactly (no credentials, backslashes or ports). It will not be linked.")
            for tab, entry in project.artifacts["tabs"].items()
            if isinstance(entry, dict) and entry.get("url") and not safe_url(entry["url"])]


def overview_levels(project):
    """Levels offered by the system-wide overview switch (Tab 1 and Tab 5): L1 and L2 only."""
    return [lv for lv in OVERVIEW_LEVELS if any(c.get("c4_level") == lv for c in project.components)]


def l3_children(project, container_id):
    return [c for c in project.components if c.get("c4_level") == "L3" and c.get("parent") == container_id]


def l3_view_paths(project, comp):
    """(before, after) for the L3 view inside one container, or None when it has no L3 components or views."""
    declared = (comp.get("views") or {}).get("L3") if isinstance(comp.get("views"), dict) else None
    if not declared and not l3_children(project, comp.get("id")):
        return None
    declared = declared if isinstance(declared, dict) else {}
    stem = str(comp.get("id", "")).lower()
    return (declared.get("before") or f"diagrams/arch-{stem}-L3-before.svg", declared.get("after") or f"diagrams/arch-{stem}-L3-after.svg")

def uc_tree_path(project):
    return project.usecases.get("tree_diagram") or "diagrams/uc-tree.svg"


def view_paths(registry, level):
    """(before, after) diagram paths for a C4 level: from registry 'views', else the default names."""
    entry = (registry.get("views") or {}).get(level)
    entry = entry if isinstance(entry, dict) else {}
    return (entry.get("before") or f"diagrams/arch-{level}-before.svg",
            entry.get("after") or f"diagrams/arch-{level}-after.svg")


def diagram_refs(project):
    """[(where, relative_path)] for every diagram the pages embed."""
    refs = [("intent", INTENT_DIAGRAM)] if project.intent else []
    if project.ucs:
        refs.append(("use case tree", uc_tree_path(project)))
    for level in overview_levels(project):
        refs += zip((f"{level} view before", f"{level} view after"), view_paths(project.registry, level))
    for comp in project.components:
        cid = comp.get("id")
        paths = l3_view_paths(project, comp)
        if paths:
            refs += zip((f"{cid} L3 before", f"{cid} L3 after"), paths)
        diagrams = (comp.get("change") or {}).get("diagrams") or {}
        refs += [(f"{cid} {key}", diagrams[key]) for key in ("before", "after", "extra") if diagrams.get(key)]
        refs += [(f"{cid} flowchart", fc["diagram"]) for fc in diagrams.get("flowchart") or [] if fc.get("diagram")]
    if project.decisions.get("tree_diagram"):
        refs.append(("decisions tree", project.decisions["tree_diagram"]))
    for dec in project.decision_list:
        refs += [(f"{dec.get('id')}/{o.get('id')}", o["wireframe"]) for o in dec.get("options", []) if o.get("wireframe")]
    refs += [(c.get("id"), c["diagram"]) for c in project.concept_list if c.get("diagram")]
    for uc in project.ucs:
        if uc.get("diagram"):
            refs.append((f"{uc.get('id')} diagram", uc["diagram"]))
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
        else:
            clean, problems = sanitize_svg(path.read_text(encoding="utf-8"))
            if problems:
                out.append(Finding("error", "unsafe-svg", where, f"{rel}: {'; '.join(sorted(set(problems)))}. Re-export a plain SVG."))
    for uc in project.ucs:
        if not uc.get("diagram"):
            continue
        clean, _ = read_diagram(project.root, uc["diagram"])
        if clean:
            fragments = set(re.findall(r'data-fragment="([^"]+)"', clean))
            steps = set(re.findall(r'data-step="([^"]+)"', clean))
            changes = uc.get("step_changes") if isinstance(uc.get("step_changes"), dict) else {}
            out += [Finding("warning", "step-change-unknown", f"usecases.json {uc.get('id')} step_changes.{step}",
                            f"{uc['diagram']} has no message with data-step=\"{step}\". Check the step label.")
                    for step in changes if steps and step not in steps]
            out += [Finding("error", "no-fragment", f"usecases.json {uc.get('id')}/{flow.get('id')}",
                            f"{uc['diagram']} has no alt fragment data-fragment=\"{flow.get('id')}\" for this {flow.get('kind')} flow.")
                    for flow in uc.get("flows") or [] if flow.get("kind") in ("bad", "edge") and flow.get("id") not in fragments]
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
    ("usecases.json step_changes", check_step_changes),
    ("usecases.json steps", check_big_project),
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
        self.reg = string_ids(project.components)
        self.ucs = string_ids(project.ucs)
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
        """(intent_html, solution_html): numbered tabs, current / linked / disabled, grouped Intent vs Solution."""
        def item(tab):
            number, label_en = self.ui_en[f"nav.{tab}"].split(" ", 1)
            label_th = self.ui_th.get(f"nav.{tab}", self.ui_en[f"nav.{tab}"]).split(" ", 1)[1]
            slots = dict(num=esc(number), label=self.bi(label_en, label_th))
            if tab == current:
                return self.part("nav_current", **slots)
            if self.tab_url(tab):
                return self.part("nav_link", href=esc(self.tab_url(tab), quote=True), **slots)
            return self.part("nav_disabled", tooltip=esc(self.ui_plain("nav.disabled"), quote=True), **slots)
        return item("intent"), "".join(item(tab) for tab in TABS[1:])

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

    def diagram(self, rel, alt="", step_changes=None):
        """Inline, sanitized SVG with an expand button (placeholder box when the file is missing or unusable)."""
        if not rel:
            return ""
        clean, _ = read_diagram(self.p.root, rel)
        if not clean:
            return self.part("diagram_missing", file=esc(rel))
        if step_changes:
            clean, _ = mark_step_changes(clean, step_changes)
        width = svg_width(clean)
        end = clean.index(">")
        head = re.sub(r'\s(?:width|height)="[^"]*"', "", clean[:end])
        svg = f'{head} style="max-width:{width}px"{clean[end:]}' if width else clean
        expand = "" if self.print_mode else self.part("expand_button", label=esc(self.ui_plain("diagram.expand"), quote=True))
        return self.part("diagram", svg=svg, maxw=width or 1200, expand=expand)

    def dictionary(self, cid, change):
        """Data dictionary: one collapsed block per new or changed table (columns, allowed values, samples, example rows)."""
        tables = (change.get("data_dictionary") or {}).get("tables") or []
        blocks = []
        for ti, table in enumerate(tables):
            base = f"data_dictionary.tables.{ti}"
            rows, examples = [], []
            for ci, col in enumerate(table.get("columns") or []):
                status = {"new": "new", "changed": "changed", "removed": "removed"}.get(col.get("change"), "unchanged")
                marker = {"new": "+", "changed": "~", "removed": "-"}.get(col.get("change"), "")
                values = "".join(self.part("dict_value", value=esc(str(v.get("value", ""))), meaning=self.tx(cid, f"{base}.columns.{ci}.values.{vi}.meaning"))
                                 for vi, v in enumerate(col.get("values") or []))
                nullable = col.get("nullable")
                name = f'<code>{esc(str(col.get("name", "")))}</code>'
                change_badge = f'<span class="badge {status}"><span aria-hidden="true">{marker}</span> {self.ui(f"status.{status}")}</span>'
                type_code = f'<code class="item-type">{esc(str(col.get("type", "")))}</code>'
                nullable_text = "" if nullable is None else self.ui("dict.yes" if nullable else "dict.no")
                default = f'<code>{esc(str(col.get("default", "-")))}</code>'
                meaning = self.tx(cid, f"{base}.columns.{ci}.meaning")
                samples = ", ".join(f"<code>{esc(str(v))}</code>" for v in col.get("samples") or [])
                rows.append(dict(
                    cells=[f'<span class="badge {status}"><span aria-hidden="true">{marker}</span></span> {name}', type_code, nullable_text, default, meaning, values, samples],
                    title=name, meta=f"{change_badge} {type_code}", body=[(None, meaning), ("dict.allowed", values), ("dict.samples", samples)],
                    footer=f'{self.ui("dict.nullable")}: {nullable_text} &middot; {self.ui("dict.default")}: {default}',
                    flip=[("dict.nullable", nullable_text), ("dict.default", default), ("dict.meaning", meaning), ("dict.allowed", values), ("dict.samples", samples)]))
                example = col.get("example")
                if isinstance(example, dict):
                    examples.append(self.part("dict_example", column=esc(str(col.get("name", ""))), before=esc(str(example.get("before", ""))), after=esc(str(example.get("after", "")))))
            sample = table.get("sample_rows") or {}
            sample_html = self.part(
                "dict_samples", heads="".join(f"<th>{esc(str(h))}</th>" for h in sample.get("columns") or []),
                rows="".join("<tr>" + "".join(f"<td>{esc(str(cell))}</td>" for cell in row) + "</tr>" for row in sample.get("rows") or [])) if sample.get("rows") else ""
            status = table.get("status", "changed")
            blocks.append(self.part(
                "dict_table", open_attr=self.open_attr(), table=esc(str(table.get("table", ""))), badge=self.part("badge", status=esc(status), label=self.ui(f"status.{status}")),
                count=self.ui_fmt("dict.columns", n=len(table.get("columns") or [])),
                grid=self.table_set("dict-cols", ["dict.name", "dict.type", "dict.nullable", "dict.default", "dict.meaning", "dict.allowed", "dict.samples"], rows, ' class="nowrap"'),
                examples="".join(examples), sample_rows=sample_html))
        return self.part("dictionary", tables="".join(blocks)) if blocks else ""

    def file_item(self, entry):
        status, marker = FILE_CHANGES.get(entry["change"], FILE_CHANGES["modified"])
        key = f"filechange.{entry['change'] if entry['change'] in FILE_CHANGES else 'modified'}"
        word = self.bi(self.ui_en[key], self.ui_th.get(key))
        path = f"<code>{esc(entry['from'])}</code> &rarr; <code>{esc(entry['path'])}</code>" if entry["change"] == "renamed" and entry.get("from") else f"<code>{esc(entry['path'])}</code>"
        return self.part("file_item", status=status, marker=esc(marker).replace("\u2192", "&rarr;"), word=word, path=path,
                         cls="file-removed" if entry["change"] == "removed" else "file")

    def files_summary(self, entries):
        """'Files changed (4): 2 added, 1 modified' in both languages."""
        counts = {kind: sum(1 for e in entries if e["change"] == kind) for kind in FILE_CHANGES}
        for e in entries:
            if e["change"] not in FILE_CHANGES:
                counts["modified"] += 1
        def line(ui):
            parts = ", ".join(ui[f"filesummary.{kind}"].format(n=n) for kind, n in counts.items() if n)
            return f"{ui['t5.files']} ({len(entries)}): {parts}"
        return self.bi(line(self.ui_en), line({**self.ui_en, **self.ui_th}))

    def table_set(self, cls, columns, rows, first_cell_attrs=""):
        """One set of rows, three renderings: a normal table (wide screens), a flipped table (item per column) and
        compact item cards (narrow screens). CSS picks one; all are plain HTML.
        Each row: cells (per column; a cell may be (html, css_class)), title, meta (badges next to the name), badge (card only),
        body [(label_key|None, html[, css_class])], footer (html), flip [(label_key, html[, css_class])]."""
        def split(cell):
            return cell if isinstance(cell, tuple) else (cell, "")

        def td(j, cell):
            html, css = split(cell)
            cls_attr = f' class="{css}"' if css else ""
            return f"<td{first_cell_attrs if j == 0 else ''}{cls_attr}>{html}</td>"

        def flip_value(row, key):
            entry = next((e for e in row["flip"] if e[0] == key), None)
            return (entry[1], entry[2] if len(entry) > 2 else "") if entry else ("", "")

        head = "".join(f"<th>{self.ui(key)}</th>" for key in columns)
        normal = "".join("<tr>" + "".join(td(j, c) for j, c in enumerate(r["cells"])) + "</tr>" for r in rows)
        flip_head = "".join(self.part("flip_title", title=r["title"], meta=r.get("meta", "")) for r in rows)
        keys = list(dict.fromkeys(entry[0] for r in rows for entry in r["flip"]))
        flip_rows = "".join(self.part("flip_row", label=self.ui(key), cells="".join(
            self.part("flip_cell", attrs=f' class="{css}"' if css else "", content=html) for html, css in (flip_value(r, key) for r in rows))) for key in keys)

        def line(entry):
            key, html = entry[0], entry[1]
            css = f" {entry[2]}" if len(entry) > 2 else ""
            return f'<div class="item-body{css}">{html}</div>' if key is None else f'<div class="item-line{css}"><span class="item-label">{self.ui(key)}</span> {html}</div>'

        cards = "".join(self.part(
            "item_card", title=r["title"], meta=r.get("meta", ""), badge=r.get("badge", ""),
            body="".join(line(e) for e in r.get("body", []) if e[1]),
            footer=f'<footer class="item-foot">{r["footer"]}</footer>' if r.get("footer") else "") for r in rows)
        return self.part("tblset", cls=cls, set=cls or "plain", head=head, rows=normal, flip_head=flip_head, flip_rows=flip_rows, cards=cards,
                         label_view=esc(self.ui_plain("tbl.view"), quote=True))

    def svg_root_id(self, rel):
        clean, _ = read_diagram(self.p.root, rel) if rel else (None, [])
        match = re.search(r'<svg[^>]*\sid="([^"]+)"', clean or "")
        return match.group(1) if match else ""

    def pair(self, before_rel, after_rel, attrs=""):
        """Before/after diagrams side by side with an 'Expand both' button (compare mode in the viewer)."""
        compare = "" if self.print_mode else self.part("compare_button")
        return self.part("pair", attrs=attrs, compare=compare, before=self.diagram(before_rel), after=self.diagram(after_rel))

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
        """One before/after data-flow pair per overview level (L1, L2); the level buttons swap the pair."""
        comps = self.p.components
        counts = {st: sum(1 for c in comps if c.get("status") == st) for st in CHANGE_STATUSES}
        levels = overview_levels(self.p)
        default = "L2" if "L2" in levels else (levels[0] if levels else "")
        views = "".join(self.pair(*view_paths(self.p.registry, level), attrs=f'data-level="{level}" ' + ("" if self.print_mode or level == default else "hidden"))
                        for level in levels)
        buttons = "".join(self.part("level_button", level=level, pressed=str(level == default).lower(), label=self.ui(f"level.{level}")) for level in levels)
        return self.part("before_after", heading=self.ui(heading_key), level_buttons=buttons, views=views,
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
        outcomes = "".join(self.part("outcome", id=esc(o.get("id", "")), text=self.tx(o.get("id"), "text"))
                           for o in self.p.usecases["outcomes"])
        uc_table = self.table_set("uc-table", ["col.usecase", "col.tier", "col.description", "col.traced"],
                                  [self._uc_row(u) for tier in TIERS for u in self.p.ucs if u.get("tier") == tier])
        impact_rows = []
        for c in self.p.components:
            if c.get("status") not in ("changed", "new", "removed"):
                continue
            name, change = self.comp_name(c.get("id")), self.tx(c.get("id"), "what")
            level = f'<span class="badge unchanged">C4 {esc(c.get("c4_level", ""))}</span>'
            ucs = self.refs([u["id"] for u in affected if u.get("caused_by") == c.get("id")]) or self.ui("t1.none")
            impact_rows.append(dict(cells=[f"<strong>{name}</strong> {level}", change, ucs], title=f"<strong>{name}</strong>", meta=level, body=[(None, change)],
                                    footer=f'{self.ui("col.affected_uc")}: {ucs}', flip=[("col.change", change), ("col.affected_uc", ucs)]))
        impact_table = self.table_set("", ["col.component", "col.change", "col.affected_uc"], impact_rows)
        crud_table = self.table_set("crud-table", ["col.entity", "crud.create", "crud.read", "crud.update", "crud.delete"],
                                    [self.crud_row(r) for r in self.p.usecases["crud"]])
        slots = dict(recap=recap, issues=issues, outcomes=outcomes, tree=self.diagram(uc_tree_path(self.p), "outcome to use cases"),
                     uc_table=uc_table, impact_table=impact_table, crud_table=crud_table, overview=self.before_after("t1.overview"))
        return slots, ""

    def _uc_row(self, uc):
        """One UC row for the table, the flipped table and the item card: the readable name leads."""
        uid, tier = uc.get("id"), uc.get("tier", "Main")
        if tier == "Affected":
            traced = self.comp_name(uc.get("caused_by"))
        else:
            ids = [o.get("id") for o in self.p.usecases["outcomes"] if uid in (o.get("ucs") or []) and isinstance(o.get("id"), str)]
            traced = f'{self.ui("t1.outcome_ref")} {esc(", ".join(ids))}' if ids else "-"
        url = self.tab_url("tab4")
        name = self.tx(uid, "title")
        valid = isinstance(uid, str)
        title = (self.part("uc_link", href=esc(f"{url}#{uid}", quote=True), id=esc(uid), name=name) if url and valid
                 else self.part("uc_plain", id=esc(uid if valid else ""), name=name))
        pill = f'<span class="pill {esc(tier.lower())}">{self.ui(f"tier.{tier}")}</span>'
        description = self.tx(uid, "summary")
        open_link = self.part("ref_link", href=esc(f"{url}#{uid}", quote=True), label=f'{self.ui("t1.open")} &rarr;') if url and valid else ""
        footer = f'{self.ui("t1.from")} {traced}' + (f" &middot; {open_link}" if open_link else "")
        return dict(cells=[title, pill, description, traced], title=title, badge=pill, body=[(None, description)], footer=footer,
                    flip=[("col.tier", pill), ("col.description", description), ("col.traced", traced)])

    def _crud_cell(self, value):
        """(html, css_class) for one CRUD cell: a UC with its name, an n/a reason, or a gap."""
        value = (value or "").strip()
        if re.fullmatch(r"UC-\d+", value):
            return self.uc_label(value), ""
        if value.startswith("n/a"):
            reason = re.sub(r"^n/a\s*[:(]?\s*", "", value).rstrip(")")
            return f'<span class="badge unchanged">n/a</span> <em class="na-reason">{esc(reason)}</em>', "na"
        return f'<span class="badge changed">{self.ui("crud.gap")}</span>', "gap"

    def uc_label(self, uid):
        """'UC-02 · Update a project's status' as a link to Tab 4 (plain text when Tab 4 has no URL or the UC is unknown)."""
        name = self.tx(uid, "title") if uid in self.ucs else ""
        url = self.tab_url("tab4")
        if not name:
            return self.ref(uid)
        return (self.part("uc_link", href=esc(f"{url}#{uid}", quote=True), id=esc(uid), name=name) if url
                else self.part("uc_plain", id=esc(uid), name=name))

    def crud_row(self, row):
        """One CRUD row for the table, the flipped table and the item card."""
        entity = str(row.get("entity", ""))
        component = next((c for c in self.p.components if str(c.get("name", "")).lower() == entity.lower()), None)
        level = f'<span class="badge unchanged">C4 {esc(component.get("c4_level", ""))}</span>' if component else ""
        name = f"<strong>{esc(entity)}</strong>"
        cells = {op: self._crud_cell(row.get(op)) for op in CRUD_OPS}
        return dict(cells=[f"{name} {level}".strip()] + [cells[op] for op in CRUD_OPS], title=name, meta=level,
                    body=[(f"crud.{op}", cells[op][0], "gap" if cells[op][1] == "gap" else "") for op in CRUD_OPS],
                    flip=[(f"crud.{op}", cells[op][0], cells[op][1]) for op in CRUD_OPS])

    def build_tab2(self):
        decisions = self.p.decision_list
        is_open = [d for d in decisions if d.get("status") != "decided"]
        decided = [d for d in decisions if d.get("status") == "decided"]
        slots = dict(tree=self.diagram(self.p.decisions.get("tree_diagram"), "solution path tree"),
                     open="".join(self._decision(d) for d in is_open), decided="".join(self._decision(d) for d in decided))
        return slots, ""

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
        side = "".join(self.part("side_concept", id=esc(c.get("id", "")), title=self.tx(c.get("id"), "title"),
                                 sub=self.tx(c.get("id"), "plain") if c.get("plain") else self.tx(c.get("id"), "label")) for c in concepts)
        blocks = "".join(self.part(
            "concept", id=esc(c.get("id", "")), label=self.tx(c.get("id"), "label"), title=self.tx(c.get("id"), "title"),
            oneline=self.part("oneline", text=self.tx(c.get("id"), "plain")) if c.get("plain") else "",
            card=self._plain_card(c), analogy=self.part("analogy", text=self.tx(c.get("id"), "analogy")) if c.get("analogy") else "",
            diagram=self.diagram(c.get("diagram"), c.get("id", "")), summary=self.tx_block(c.get("id"), "summary"),
            terms=self.part("terms", text=self.tx(c.get("id"), "terms")) if c.get("terms") else "",
            related=", ".join(filter(None, [self.refs(c.get("decisions")), self.refs(c.get("ucs")), self.refs(c.get("components"))])))
            for c in concepts)
        return dict(side=side, blocks=blocks), ""

    def _plain_card(self, concept):
        rows = [(key, ui) for key, ui in (("before", "t3.before"), ("now", "t3.now"), ("why", "t3.why_matters")) if concept.get(key)]
        return self.part("plain_card", rows="".join(self.part("plain_row", label=self.ui(ui), text=self.tx(concept.get("id"), key)) for key, ui in rows)) if rows else ""

    def build_tab4(self):
        ucs = self.p.ucs
        side = {tier: "".join(self.part("side_link", id=esc(u.get("id", "")), title=self.tx(u.get("id"), "title"))
                              for u in ucs if u.get("tier") == tier) for tier in TIERS}
        ordered = [u for tier in TIERS for u in ucs if u.get("tier") == tier]
        return dict(side_main=side["Main"], side_affected=side["Affected"], ucs="".join(self._uc(u) for u in ordered)), ""

    def _uc(self, uc):
        uid, flows = uc.get("id"), uc.get("flows") or []
        rel = uc.get("diagram")
        target = esc(self.svg_root_id(rel), quote=True)
        scenario = lambda i, f: f'{self.ui("scenario." + f["kind"])}: {self.tx(uid, f"flows.{i}.title")}'  # noqa: E731
        toggles = "".join(self.part("toggle_kind", kind=kind, target=target, label=self.ui(f"t4.{kind}"))
                          for kind in ("bad", "edge") if any(f.get("kind") == kind for f in flows))
        chips = "".join(self.part("toggle_fragment", kind=esc(f["kind"]), target=target, fragment=esc(str(f.get("id", "")), quote=True), label=scenario(i, f))
                        for i, f in enumerate(flows) if f.get("kind") in ("bad", "edge"))
        kind_class = {"good": "unchanged", "bad": "changed", "edge": "new"}
        raw_changes = uc.get("step_changes") if isinstance(uc.get("step_changes"), dict) else {}
        step_changes = {str(k): v for k, v in raw_changes.items() if v in STEP_STYLE}
        touch_rows = []
        for i, f in enumerate(flows):
            kind = f.get("kind", "good")
            badge = f'<span class="badge {kind_class.get(kind, "unchanged")}">{self.ui("scenario." + kind)}</span>'
            name = f'<strong>{self.tx(uid, f"flows.{i}.title")}</strong>'
            for j, t in enumerate(f.get("touchpoints") or []):
                step, sees = esc(str(t.get("step", ""))), self.tx(uid, f"flows.{i}.touchpoints.{j}.sees")
                touch_rows.append(dict(cells=[f"{badge} {name}", step, sees], title=name, meta=badge, body=[(None, sees)],
                                       footer=f'{self.ui("t4.step")} {step}', flip=[("t4.step", step), ("t4.sees", sees)]))
        rows = self.table_set("", ["t4.scenario", "t4.step", "t4.sees"], touch_rows)
        details = "".join(self.part("detail", title=self.tx(uid, f"details.{i}.title"), diagram=self.diagram(d.get("diagram")), open_attr=self.open_attr())
                          for i, d in enumerate(uc.get("details") or []))
        return self.part(
            "uc", id=esc(uid or ""), title=self.tx(uid, "title"), tier=self.ui(f"tier.{uc.get('tier', 'Main')}"),
            source=self.ui(f"source.{uc.get('source', 'code')}"), summary=self.tx(uid, "summary"), toggles=toggles, chips=chips,
            legend=self.part("seq_legend") if step_changes else "", diagram=self.diagram(rel, step_changes=step_changes), touchpoints=rows, details=details, open_attr=self.open_attr(), track=self.changelog(uid, wrap=False))

    def build_tab5(self):
        cards = "".join(self._component(c) for c in self.p.components if c.get("status") != "unchanged" or l3_view_paths(self.p, c))
        return dict(overview=self.before_after("t5.overview"), components=cards, changelog=self.changelog()), ""

    def _component(self, comp):
        cid, change = comp.get("id"), comp.get("change") or {}
        status = comp.get("status", "unchanged")
        changing = status in CHANGING
        diagrams = change.get("diagrams") or {}
        titled = lambda title, diagram: self.part("titled", title=title, diagram=diagram)  # noqa: E731
        views = self.pair(diagrams.get("before"), diagrams.get("after")) if changing and (diagrams.get("before") or diagrams.get("after")) else ""
        kind = {"db": "db", "store": "db", "queue": "queue"}.get(comp.get("kind"), "other")
        extra = titled(self.ui(f"t5.extra_{kind}"), self.diagram(diagrams["extra"])) if changing and diagrams.get("extra") else ""
        flowcharts = "".join(titled(self.tx(cid, f"diagrams.flowchart.{i}.title"), self.diagram(fc.get("diagram"))) for i, fc in enumerate(diagrams.get("flowchart") or [])) if changing else ""
        l3 = l3_view_paths(self.p, comp)
        inside = titled(self.ui_fmt("t5.inside", name=comp.get("name", "")), self.pair(*l3)) if l3 else ""
        detail = ""
        if changing:
            data = "".join(self.part("row3", a=esc(r.get("object", "")), b=esc(r.get("change", "")), c=esc(r.get("migration", ""))) for r in change.get("data", []))
            infra = "".join(self.part("row3", a=esc(r.get("item", "")), b=esc(str(r.get("before", ""))), c=esc(str(r.get("after", "")))) for r in change.get("infra", []))
            files = file_entries(change)
            detail = self.part(
                "component_detail", what=self.tx_block(cid, "what"), why=self.tx_block(cid, "why"),
                links=", ".join(filter(None, [self.refs(change.get("decisions")), self.refs(change.get("ucs"))])),
                data=(self.part("data_table", rows=data) if data else "") + self.dictionary(cid, change), infra=self.part("infra_table", rows=infra) if infra else "",
                files=self.part("files", open_attr=self.open_attr(), summary=self.files_summary(files), items="".join(self.file_item(f) for f in files)) if files else "")
        return self.part(
            "component", id=esc(cid or ""), status=esc(status), name=esc(comp.get("name", "")), level=esc(comp.get("c4_level", "")),
            open_attr="open" if status == "changed" or self.print_mode else "", badge=self.part("badge", status=esc(status), label=self.ui(f"status.{status}")),
            views=views, extra=extra, flowcharts=flowcharts, inside=inside, detail=detail)

    def content(self, tab):
        """(page_title, header_extra, content_html) for one tab."""
        template = self.template(f"{tab}.html")  # registers the tab's parts before the build_* methods use them
        slots, header_extra = getattr(self, f"build_{tab}")()
        content = self.fill(template, **slots)
        return f"{self.p.name} · {self.tab_title(tab)}", header_extra, content

    def page(self, tab):
        title, header_extra, content = self.content(tab)
        label = self.ui("page.intent" if tab == "intent" else "page.solution")
        nav_intent, nav_solution = self.nav(tab)
        labels = {f"label_{key}": esc(self.ui_plain(f"viewer.{key}"), quote=True) for key in ("zoom_in", "zoom_out", "fit", "close", "sync_zoom", "sync_pan", "layout", "layout_side", "layout_stack")}
        return self.fill(
            self.template("_base.html"), lang=self.lang, title=esc(title), theme_css=self.theme_css,
            base_css=(TEMPLATES / "base.css").read_text(encoding="utf-8"), base_js=(TEMPLATES / "base.js").read_text(encoding="utf-8"),
            body_class="", project=esc(self.p.name), page_label=label, header_extra=header_extra, nav_intent=nav_intent, nav_solution=nav_solution,
            content=content, updated=self.updated(), **labels)

    def print_document(self, tabs):
        template = self.template("print.html")
        rendered = [self.content(t) for t in tabs]
        pages = "".join(self.part("print_page", title=esc(title), content=content) for title, _, content in rendered)
        toc = "".join(self.part("toc_item", label=self.ui(f"nav.{t}")) for t in tabs)
        return self.fill(template, lang=self.lang, title=esc(f"{self.p.name} · export"), project=esc(self.p.name),
                         theme_css=self.theme_css, base_css=(TEMPLATES / "base.css").read_text(encoding="utf-8"),
                         base_js=(TEMPLATES / "base.js").read_text(encoding="utf-8"), updated=self.updated(), toc=toc, pages=pages)


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
