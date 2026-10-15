"""A small evaluator for the DAX subset used by the measures in this model.

It reads the measure text from the TMDL files and evaluates it against the CSV extracts, so the tests check the
DAX that Power BI will run, not a Python rewrite of it. It is not the Analysis Services engine. It covers:

* filter context: column filters as value sets; filters flow from the one side to the many side of each
  relationship (single direction, as in the model), through chains such as DimPlanningArea -> DimStop -> fact;
* CALCULATE with boolean filter arguments on one column (=, IN, comparisons, ||, &&), which replace the filter
  on that column, and with table filters from PREVIOUSMONTH; a filter on the key column of a table marked as
  date table (or on a date column that is the one side of a relationship) removes the other filters on that
  table, as Power BI does;
* VAR / RETURN, measure references, SUM, COUNTROWS, DIVIDE, PERCENTILE.INC, PREVIOUSMONTH, IF, ISBLANK, BLANK,
  TRUE, FALSE, NOT, arithmetic and comparison operators;
* BLANK semantics for the cases used here: aggregations over no rows return BLANK; DIVIDE returns BLANK (or
  the alternate result) for a BLANK or zero denominator; BLANK acts as 0 in + and -, and BLANK +/- BLANK is BLANK.

Anything outside this subset raises an error instead of returning a guess.
"""
import calendar
import datetime as dt
import math
import re

import numpy as np
import pandas as pd


class DaxError(Exception):
    pass


BLANK = None

# ------------------------------------------------------------------ tokenizer

_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<comment>//[^\n]*|--[^\n]*|/\*.*?\*/)
  | (?P<string>"(?:[^"]|"")*")
  | (?P<number>\d+\.\d*|\.\d+|\d+)
  | (?P<qtable>'(?:[^']|'')+')
  | (?P<bracket>\[[^\]]+\])
  | (?P<op>&&|\|\||<=|>=|<>|==|[=<>+\-*/&(),{}])
  | (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)
""", re.X | re.S)


def tokenize(text):
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise DaxError(f"cannot tokenize at: {text[pos:pos + 30]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind in ("ws", "comment"):
            continue
        val = m.group(kind)
        if kind == "string":
            val = val[1:-1].replace('""', '"')
        elif kind == "qtable":
            val, kind = val[1:-1].replace("''", "'"), "table"
        elif kind == "bracket":
            val = val[1:-1]
        out.append((kind, val))
    out.append(("eof", None))
    return out


# ------------------------------------------------------------------ parser -> tuples

class Parser:
    def __init__(self, text):
        self.t = tokenize(text)
        self.i = 0

    def peek(self, k=0):
        return self.t[self.i + k]

    def take(self, kind=None, val=None):
        tok = self.t[self.i]
        if kind and tok[0] != kind or val is not None and (tok[1] or "").upper() != val.upper():
            raise DaxError(f"expected {kind} {val}, got {tok}")
        self.i += 1
        return tok

    def is_kw(self, word):
        tok = self.peek()
        return tok[0] == "ident" and tok[1].upper() == word

    def parse(self):
        e = self.expr()
        self.take("eof")
        return e

    def expr(self):
        if self.is_kw("VAR"):
            binds = []
            while self.is_kw("VAR"):
                self.take()
                name = self.take("ident")[1]
                self.take("op", "=")
                binds.append((name, self.expr()))
            self.take("ident", "RETURN")
            return ("var", binds, self.expr())
        return self.or_()

    def binary(self, sub, ops):
        left = sub()
        while self.peek()[0] == "op" and self.peek()[1] in ops:
            op = self.take()[1]
            left = ("bin", op, left, sub())
        return left

    def or_(self):
        return self.binary(self.and_, ("||",))

    def and_(self):
        return self.binary(self.cmp, ("&&",))

    def cmp(self):
        left = self.concat()
        while True:
            tok = self.peek()
            if tok[0] == "op" and tok[1] in ("=", "==", "<>", "<", ">", "<=", ">="):
                op = self.take()[1]
                left = ("bin", op, left, self.concat())
            elif self.is_kw("IN"):
                self.take()
                left = ("in", left, self.concat())
            else:
                return left

    def concat(self):
        return self.binary(self.add, ("&",))

    def add(self):
        return self.binary(self.mul, ("+", "-"))

    def mul(self):
        return self.binary(self.unary, ("*", "/"))

    def unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            return ("neg", self.unary())
        if self.peek() == ("op", "+"):
            self.take()
            return self.unary()
        return self.primary()

    def primary(self):
        kind, val = self.peek()
        if kind == "number":
            self.take()
            return ("lit", float(val) if "." in val else int(val))
        if kind == "string":
            self.take()
            return ("lit", val)
        if kind == "bracket":
            self.take()
            return ("measure", val)
        if kind == "table":
            self.take()
            if self.peek()[0] == "bracket":
                return ("col", val, self.take()[1])
            return ("table", val)
        if kind == "op" and val == "(":
            self.take()
            e = self.expr()
            self.take("op", ")")
            return e
        if kind == "op" and val == "{":
            self.take()
            items = []
            if self.peek() != ("op", "}"):
                items.append(self.expr())
                while self.peek() == ("op", ","):
                    self.take()
                    items.append(self.expr())
            self.take("op", "}")
            return ("set", items)
        if kind == "ident":
            self.take()
            nxt = self.peek()
            if nxt == ("op", "("):
                self.take()
                args = []
                if self.peek() != ("op", ")"):
                    args.append(self.expr())
                    while self.peek() == ("op", ","):
                        self.take()
                        args.append(self.expr())
                self.take("op", ")")
                return ("call", val.upper(), args)
            if nxt[0] == "bracket":
                return ("col", val, self.take()[1])
            return ("name", val)
        raise DaxError(f"unexpected token {kind} {val!r}")


def parse(text):
    return Parser(text).parse()


# ------------------------------------------------------------------ filter context

class Context:
    """filters: {(table, column): frozenset of allowed values}"""

    def __init__(self, model, filters=None, cache=None):
        self.model = model
        self.filters = {k: frozenset(v) for k, v in (filters or {}).items()}
        self._rows = {}
        self._cache = cache if cache is not None else {}
        self._key = frozenset(self.filters.items())

    def is_filtered(self, table, _seen=None):
        if any(t == table for t, _ in self.filters):
            return True
        _seen = (_seen or set()) | {table}
        return any(r.to_table not in _seen and self.is_filtered(r.to_table, _seen)
                   for r in self.model.relationships if r.from_table == table)

    def rows(self, table):
        if table in self._rows:
            return self._rows[table]
        if (table, self._key) in self._cache:
            self._rows[table] = self._cache[(table, self._key)]
            return self._rows[table]
        df = self.model.frames[table]
        mask = np.ones(len(df), dtype=bool)
        for (t, c), vals in self.filters.items():
            if t == table:
                mask &= np.asarray(df[c].isin(list(vals)), dtype=bool)
        for r in self.model.relationships:
            if r.from_table == table and self.is_filtered(r.to_table):
                keys = self.rows(r.to_table)[r.to_column]
                mask &= np.asarray(df[r.from_column].isin(list(keys)), dtype=bool)
        out = df[mask]
        self._rows[table] = self._cache[(table, self._key)] = out
        return out


# ------------------------------------------------------------------ evaluator

def _is_blank(v):
    return v is None or (isinstance(v, float) and math.isnan(v))


def _columns_in(node, acc):
    if isinstance(node, tuple):
        if node[0] == "col":
            acc.add((node[1], node[2]))
        for part in node[1:]:
            if isinstance(part, (tuple, list)):
                for p in (part if isinstance(part, list) else [part]):
                    _columns_in(p, acc)
    return acc


class Evaluator:
    def __init__(self, model):
        self.model = model
        self._ast = {}
        self._rows_cache = {}   # visible rows per (table, filter context); the frames never change

    def ast(self, measure):
        if measure not in self._ast:
            if measure not in self.model.measures:
                raise DaxError(f"unknown measure [{measure}]")
            self._ast[measure] = parse(self.model.measures[measure].expression)
        return self._ast[measure]

    def measure(self, name, filters=None):
        return self.eval(self.ast(name), Context(self.model, filters, self._rows_cache), {}, None)

    # row: {(table, column): value} when evaluating a boolean filter predicate for one candidate value
    def eval(self, n, ctx, env, row):
        kind = n[0]
        if kind == "lit":
            return n[1]
        if kind == "var":
            env = dict(env)
            for name, e in n[1]:
                env[name.upper()] = self.eval(e, ctx, env, row)
            return self.eval(n[2], ctx, env, row)
        if kind == "name":
            if n[1].upper() not in env:
                raise DaxError(f"unknown variable {n[1]}")
            return env[n[1].upper()]
        if kind == "measure":
            return self.eval(self.ast(n[1]), ctx, {}, None)
        if kind == "col":
            if row is not None and (n[1], n[2]) in row:
                return row[(n[1], n[2])]
            raise DaxError(f"column {n[1]}[{n[2]}] used without a row context")
        if kind == "neg":
            v = self.eval(n[1], ctx, env, row)
            return BLANK if _is_blank(v) else -v
        if kind == "bin":
            return self.binop(n[1], self.eval(n[2], ctx, env, row), self.eval(n[3], ctx, env, row))
        if kind == "in":
            v = self.eval(n[1], ctx, env, row)
            if n[2][0] != "set":
                raise DaxError("IN needs a {...} list here")
            return any(self.binop("=", v, self.eval(x, ctx, env, row)) for x in n[2][1])
        if kind == "call":
            return self.call(n[1], n[2], ctx, env, row)
        raise DaxError(f"cannot evaluate {kind}")

    @staticmethod
    def binop(op, a, b):
        if op in ("+", "-"):
            if _is_blank(a) and _is_blank(b):
                return BLANK
            a, b = (0 if _is_blank(a) else a), (0 if _is_blank(b) else b)
            return a + b if op == "+" else a - b
        if op in ("*", "/"):
            if _is_blank(a) or _is_blank(b):
                return BLANK
            if op == "/" and b == 0:
                raise DaxError("division by zero with /; the measures use DIVIDE")
            return a * b if op == "*" else a / b
        if op == "&&":
            return bool(a) and bool(b)
        if op == "||":
            return bool(a) or bool(b)
        if op == "&":
            return f"{'' if _is_blank(a) else a}{'' if _is_blank(b) else b}"
        if _is_blank(a) or _is_blank(b):
            raise DaxError("comparison with BLANK is outside the supported subset")
        return {"=": a == b, "==": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]

    def _column_values(self, n, ctx):
        if n[0] != "col":
            raise DaxError(f"expected a column reference, got {n[0]}")
        table, col = n[1], n[2]
        if table not in self.model.frames or col not in self.model.frames[table]:
            raise DaxError(f"unknown column {table}[{col}]")
        return ctx.rows(table)[col]

    def call(self, fn, args, ctx, env, row):
        ev = lambda a: self.eval(a, ctx, env, row)  # noqa: E731
        if fn == "SUM":
            vals = self._column_values(args[0], ctx).dropna()
            if vals.empty:
                return BLANK
            total = vals.sum()
            return total.item() if hasattr(total, "item") else total
        if fn == "COUNTROWS":
            if args[0][0] not in ("table", "name"):
                raise DaxError("COUNTROWS needs a table name")
            table = args[0][1]
            if table not in self.model.frames:
                raise DaxError(f"unknown table {table}")
            n = len(ctx.rows(table))
            return BLANK if n == 0 else n
        if fn == "DIVIDE":
            a, b = ev(args[0]), ev(args[1])
            alt = ev(args[2]) if len(args) > 2 else BLANK
            if _is_blank(b) or b == 0:
                return alt
            if _is_blank(a):
                return BLANK
            return a / b
        if fn == "PERCENTILE.INC":
            vals = self._column_values(args[0], ctx).dropna().astype(float).to_numpy()
            k = ev(args[1])
            if not 0 <= k <= 1:
                raise DaxError("PERCENTILE.INC k must be in [0, 1]")
            if len(vals) == 0:
                return BLANK
            return float(np.percentile(vals, k * 100, method="linear"))
        if fn == "IF":
            cond = ev(args[0])
            if cond:
                return ev(args[1])
            return ev(args[2]) if len(args) > 2 else BLANK
        if fn == "ISBLANK":
            return _is_blank(ev(args[0]))
        if fn == "BLANK":
            return BLANK
        if fn == "TRUE":
            return True
        if fn == "FALSE":
            return False
        if fn == "NOT":
            return not ev(args[0])
        if fn == "CALCULATE":
            return self.calculate(args, ctx, env)
        if fn == "PREVIOUSMONTH":
            return ("tablefilter",) + self.previous_month(args[0], ctx)
        raise DaxError(f"function {fn} is outside the supported subset")

    def previous_month(self, colnode, ctx):
        if colnode[0] != "col":
            raise DaxError("PREVIOUSMONTH needs a date column")
        table, col = colnode[1], colnode[2]
        visible = [d for d in ctx.rows(table)[col] if d is not None]
        if not visible:
            return table, col, frozenset()
        first = min(visible)
        y, m = (first.year, first.month - 1) if first.month > 1 else (first.year - 1, 12)
        lo, hi = dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])
        allv = self.model.frames[table][col]
        return table, col, frozenset(d for d in allv if d is not None and lo <= d <= hi)

    def removes_table_filters(self, table, col):
        """Power BI adds ALL(table) when CALCULATE filters the date column of a table marked as date table, and
        also when that column is a date column on the one side of a relationship."""
        if self.model.date_tables.get(table) == col:
            return True
        meta = self.model.meta.tables[table].columns.get(col)
        return bool(meta and meta.data_type == "dateTime" and any(
            r.to_table == table and r.to_column == col for r in self.model.relationships))

    def filter_arg(self, a, ctx, env):
        """Turn one CALCULATE filter argument into (table, column, allowed values)."""
        if a[0] == "call" and a[1] == "PREVIOUSMONTH":
            return self.previous_month(a[2][0], ctx)
        cols = _columns_in(a, set())
        if len(cols) != 1:
            raise DaxError(f"a boolean filter must reference exactly one column, found {sorted(cols)}")
        (table, col), = cols
        if table not in self.model.frames or col not in self.model.frames[table]:
            raise DaxError(f"unknown column {table}[{col}]")
        # Boolean filter = FILTER(ALL(table[col]), predicate): test every value of the column.
        candidates = pd.unique(self.model.frames[table][col].dropna())
        keep = frozenset(v for v in candidates if self.eval(a, ctx, env, {(table, col): v}))
        return table, col, keep

    def calculate(self, args, ctx, env):
        new_filters = [self.filter_arg(a, ctx, env) for a in args[1:]]
        filters = dict(ctx.filters)
        for table, col, _ in new_filters:
            filters.pop((table, col), None)
            if self.removes_table_filters(table, col):
                for k in [k for k in filters if k[0] == table]:
                    filters.pop(k)
        added = set()
        for table, col, vals in new_filters:
            key = (table, col)
            # Two arguments on the same column intersect; an argument replaces the outer filter on its column.
            filters[key] = filters[key] & vals if key in added else vals
            added.add(key)
        return self.eval(args[0], Context(self.model, filters, self._rows_cache), env, None)
