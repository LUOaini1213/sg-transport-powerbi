"""A reader for the subset of TMDL used in this project.

It is not a full TMDL parser; the authoritative syntax check is Microsoft's own TmdlSerializer (see
tools/check_tmdl.ps1, run in CI). This reader only extracts what the tests need: tables, columns (data type,
source column, key flag), measures (DAX text), the CSV file each partition loads, the date-table flag and the
relationships.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Column:
    name: str
    data_type: str = "string"
    source_column: str = ""
    is_key: bool = False
    is_hidden: bool = False
    sort_by: str = ""


@dataclass
class Measure:
    name: str
    expression: str
    table: str
    format_string: str = ""
    description: str = ""


@dataclass
class Table:
    name: str
    data_category: str = ""
    columns: dict = field(default_factory=dict)
    measures: dict = field(default_factory=dict)
    partition_source: str = ""
    description: str = ""

    @property
    def csv_file(self):
        files = set(re.findall(r'"([A-Za-z0-9_]+\.csv)"', self.partition_source))
        if len(files) != 1:
            raise ValueError(f"table {self.name}: expected one CSV file in the partition, found {sorted(files)}")
        return files.pop()

    @property
    def key_column(self):
        keys = [c.name for c in self.columns.values() if c.is_key]
        return keys[0] if keys else None


@dataclass
class Relationship:
    name: str
    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass
class SemanticModel:
    tables: dict
    relationships: list
    expressions: dict

    @property
    def measures(self):
        return {m.name: m for t in self.tables.values() for m in t.measures.values()}


def _indent(line):
    return len(line) - len(line.lstrip("\t"))


def _name(text):
    """Parse a possibly quoted TMDL object name at the start of text; return (name, rest)."""
    text = text.strip()
    if text.startswith("'"):
        i, out = 1, []
        while i < len(text):
            if text[i] == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                return "".join(out), text[i + 1:]
            out.append(text[i])
            i += 1
        raise ValueError(f"unterminated name: {text}")
    m = re.match(r"[^\s=:.]+", text)
    return m.group(0), text[m.end():]


def _qualified(text):
    """Table.Column with either part optionally quoted."""
    t, rest = _name(text)
    if not rest.startswith("."):
        raise ValueError(f"expected Table.Column, got {text}")
    c, rest = _name(rest[1:])
    return t, c


def _strip_block(lines):
    lines = [ln.rstrip() for ln in lines]
    while lines and not lines[-1].strip():
        lines.pop()
    body = [ln for ln in lines if ln.strip()]
    if not body:
        return ""
    cut = min(_indent(ln) for ln in body)
    return "\n".join(ln[cut:] for ln in lines)


def _value(text):
    v = text.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1].replace('""', '"')
    return v


def _read_expression(lines, i, header_rest, min_indent):
    """Expression after '='; same line, or the following lines indented deeper than min_indent."""
    first = header_rest.strip()
    if first and first != "```":
        return first, i + 1
    j = i + 1
    block = []
    if first == "```":
        while not lines[j].strip().startswith("```"):
            block.append(lines[j])
            j += 1
        return _strip_block(block), j + 1
    while j < len(lines) and (not lines[j].strip() or _indent(lines[j]) > min_indent):
        block.append(lines[j])
        j += 1
    return _strip_block(block), j


def parse_folder(definition: Path) -> SemanticModel:
    definition = Path(definition)
    tables, relationships, expressions = {}, [], {}
    files = sorted((definition / "tables").glob("*.tmdl")) + [definition / "relationships.tmdl",
                                                            definition / "expressions.tmdl"]
    for path in files:
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        if any(ln.startswith(" ") for ln in lines if ln.strip()):
            raise ValueError(f"{path.name}: TMDL indentation must use tabs")
        i, doc, table, obj = 0, [], None, None
        while i < len(lines):
            line = lines[i]
            s = line.strip()
            ind = _indent(line)
            if not s:
                i += 1
                continue
            if s.startswith("///"):
                doc.append(s[3:].strip())
                i += 1
                continue
            word = s.split()[0]
            if ind == 0 and word == "table":
                name, _ = _name(s[len("table"):])
                table = tables[name] = Table(name, description=" ".join(doc))
                obj, doc = table, []
            elif ind == 0 and word == "relationship":
                rel_name = s.split(None, 1)[1]
                props = {}
                i += 1
                while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) >= 1):
                    if lines[i].strip():
                        k, _, v = lines[i].strip().partition(":")
                        props[k.strip()] = v.strip()
                    i += 1
                ft, fc = _qualified(props["fromColumn"])
                tt, tc = _qualified(props["toColumn"])
                relationships.append(Relationship(rel_name, ft, fc, tt, tc))
                doc = []
                continue
            elif ind == 0 and word == "expression":
                name, rest = _name(s[len("expression"):])
                expr, i = _read_expression(lines, i, rest.split("=", 1)[1], 0)
                expressions[name] = expr
                doc = []
                continue
            elif ind == 1 and table is not None and word == "column":
                name, _ = _name(s[len("column"):])
                obj = table.columns[name] = Column(name)
                doc = []
            elif ind == 1 and table is not None and word == "measure":
                name, rest = _name(s[len("measure"):])
                expr, i = _read_expression(lines, i, rest.split("=", 1)[1], 2)
                obj = table.measures[name] = Measure(name, expr, table.name, description=" ".join(doc))
                doc = []
                continue
            elif ind == 1 and table is not None and word == "partition":
                obj = "partition"
                doc = []
            elif ind == 2 and obj == "partition" and s.startswith("source"):
                expr, i = _read_expression(lines, i, s.split("=", 1)[1], 2)
                table.partition_source = expr
                continue
            elif ind == 1 and table is not None and s.startswith("dataCategory:"):
                table.data_category = _value(s.split(":", 1)[1])
            elif ind == 2 and isinstance(obj, Column):
                k, sep, v = s.partition(":")
                k = k.strip()
                if k == "dataType":
                    obj.data_type = _value(v)
                elif k == "sourceColumn":
                    obj.source_column = _value(v)
                elif k == "sortByColumn":
                    obj.sort_by = _name(v)[0]
                elif k == "isKey":
                    obj.is_key = not sep or _value(v).lower() == "true"
                elif k == "isHidden":
                    obj.is_hidden = not sep or _value(v).lower() == "true"
            elif ind == 2 and isinstance(obj, Measure):
                k, _, v = s.partition(":")
                if k.strip() == "formatString":
                    obj.format_string = _value(v)
            i += 1
    return SemanticModel(tables, relationships, expressions)
