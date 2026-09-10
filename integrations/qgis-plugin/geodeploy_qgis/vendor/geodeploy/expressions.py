"""QGIS expressions → MapLibre expressions, over a subset that is stated rather than discovered.

## Why this exists

Rule-based rendering is the renderer most real QGIS projects reach for, and a rule is a FILTER: it
is an expression over a feature's attributes. The same is true of a layer's subset string, and of
every data-defined property beyond the two (colour, size) GeoDeploy special-cases. So the single
thing standing between GeoDeploy and most of QGIS's remaining symbology is a way to turn one
expression language into the other.

MapLibre's expression language is a close relative — both are trees of operators over feature
properties — but it is smaller, and the gap is not a detail. `shapeburst`-style constructs have no
web equivalent at all; `geometry_generator` produces new geometry; `@map_scale` is a rendering
variable rather than a property. **The rule this module follows is that the subset is declared and
anything outside it raises `Unsupported` naming the construct**, because the alternative — quietly
translating to something close — publishes a map that does not match the one the author was looking
at, and they find out from a reader.

## Why it lives in the client package

Three consumers need it and none of them can pip-install: the QGIS plugin (which vendors this
package), the CLI, and any script driving an instance. Zero dependencies, Python 3.9 floor, exactly
like the rest of `geodeploy/`.

The SERVER deliberately does not use it. A rule arrives already translated, as a MapLibre filter
inside `style.rules`, so there is one translator rather than two that would eventually disagree —
the same reasoning that keeps classification maths in `services/symbology.py` alone.

## The subset

Operators
    = == <> != < <= > >=            comparison
    AND OR NOT                      boolean
    IN / NOT IN                     membership
    IS NULL / IS NOT NULL           presence (see the note on `_is_null`)
    BETWEEN / NOT BETWEEN           range
    LIKE / ILIKE / NOT LIKE         prefix, suffix and contains patterns only
    + - * / %                       arithmetic
    ||                              string concatenation
    CASE WHEN … THEN … ELSE … END   conditional

Functions
    lower upper length coalesce concat abs round floor ceil sqrt ln log10
    to_string to_int to_real to_number if min max strpos substr left right replace

Values
    'text'  "field"  bare_field  123  1.5  TRUE  FALSE  NULL  $id
"""
from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Tuple


class Unsupported(ValueError):
    """A construct outside the declared subset.

    Carries `construct` so a caller can say *which* part of somebody's expression it could not take,
    which is the difference between "this rule did not travel" and a fidelity report worth reading.
    """

    def __init__(self, construct: str, detail: str = ""):
        self.construct = construct
        super().__init__("{0} is not supported{1}".format(construct, ": " + detail if detail else ""))


# ── Tokens ───────────────────────────────────────────────────────────────────────────────────────

_KEYWORDS = {"and", "or", "not", "like", "ilike", "in", "is", "null", "true", "false",
             "case", "when", "then", "else", "end", "between"}

#: Longest first — otherwise `<=` tokenises as `<` followed by `=`, `<>` as `<` then `>`, and `==`
#: as two assignments. QGIS accepts both `=` and `==` for equality, so both are here.
_OPERATORS = ("<>", "!=", ">=", "<=", "==", "||", "=", "<", ">", "+", "-", "*", "/", "%",
              "(", ")", ",")

_NUMBER = re.compile(r"\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?")
_WORD = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


class _Token(object):
    __slots__ = ("kind", "value", "pos")

    def __init__(self, kind: str, value: Any, pos: int):
        self.kind = kind        # str | number | field | word | op | end
        self.value = value
        self.pos = pos

    def __repr__(self):         # pragma: no cover - debugging only
        return "<{0} {1!r}>".format(self.kind, self.value)


def tokenize(text: str) -> List[_Token]:
    """`text` split into tokens. Raises `Unsupported` for characters the subset has no meaning for."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if text.startswith("--", i):                    # QGIS line comment
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if text.startswith("/*", i):                    # QGIS block comment
            j = text.find("*/", i + 2)
            if j < 0:
                raise Unsupported("an unterminated comment")
            i = j + 2
            continue
        if ch == "'":                                   # string literal, '' escapes a quote
            j, buf = i + 1, []
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(text[j])
                j += 1
            else:
                raise Unsupported("an unterminated string")
            out.append(_Token("str", "".join(buf), i))
            i = j + 1
            continue
        if ch == '"':                                   # quoted field name, "" escapes a quote
            j, buf = i + 1, []
            while j < n:
                if text[j] == '"':
                    if j + 1 < n and text[j + 1] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    break
                buf.append(text[j])
                j += 1
            else:
                raise Unsupported("an unterminated field name")
            out.append(_Token("field", "".join(buf), i))
            i = j + 1
            continue
        if ch == "$":                                   # $id and friends
            m = _WORD.match(text, i + 1)
            if not m:
                raise Unsupported("$", "a bare $ is not a value")
            out.append(_Token("word", "$" + m.group(0), i))
            i = m.end()
            continue
        if ch == "@":                                   # @map_scale, @layer_name, …
            m = _WORD.match(text, i + 1)
            name = "@" + (m.group(0) if m else "")
            raise Unsupported(name, "a rendering variable has no feature-level equivalent")
        m = _NUMBER.match(text, i)
        if m:
            raw = m.group(0)
            value = float(raw) if ("." in raw or "e" in raw or "E" in raw) else int(raw)
            out.append(_Token("number", value, i))
            i = m.end()
            continue
        m = _WORD.match(text, i)
        if m:
            word = m.group(0)
            out.append(_Token("word", word, i))
            i = m.end()
            continue
        for op in _OPERATORS:
            if text.startswith(op, i):
                out.append(_Token("op", op, i))
                i += len(op)
                break
        else:
            raise Unsupported(repr(ch), "unexpected character at position {0}".format(i))
    out.append(_Token("end", None, n))
    return out


# ── Functions ────────────────────────────────────────────────────────────────────────────────────

#: QGIS function → (MapLibre operator, minimum args, maximum args or None for any). Only the ones
#: whose semantics MATCH; a function that is merely similarly named is worse than an unsupported one.
_FUNCTIONS = {
    "lower": ("downcase", 1, 1),
    "upper": ("upcase", 1, 1),
    "length": ("length", 1, 1),
    "coalesce": ("coalesce", 1, None),
    "concat": ("concat", 1, None),
    "abs": ("abs", 1, 1),
    "round": ("round", 1, 1),
    "floor": ("floor", 1, 1),
    "ceil": ("ceil", 1, 1),
    "sqrt": ("sqrt", 1, 1),
    "ln": ("ln", 1, 1),
    "log10": ("log10", 1, 1),
    "to_string": ("to-string", 1, 1),
    "to_int": ("to-number", 1, 1),
    "to_real": ("to-number", 1, 1),
    "to_number": ("to-number", 1, 1),
    "min": ("min", 1, None),
    "max": ("max", 1, None),
}


# ── Parser ───────────────────────────────────────────────────────────────────────────────────────

class _Parser(object):
    """Precedence-climbing over the token list. One pass, no AST — it emits MapLibre directly.

    There is no intermediate tree because nothing needs one: the output language has the same shape
    as the input (a prefix tree of operators over properties), so a second representation would only
    be somewhere for the two to drift apart.
    """

    def __init__(self, tokens: Sequence[_Token]):
        self.tokens = list(tokens)
        self.i = 0

    # -- token helpers ----------------------------------------------------------------------------

    @property
    def tok(self) -> _Token:
        return self.tokens[self.i]

    def next(self) -> _Token:
        t = self.tokens[self.i]
        self.i += 1
        return t

    def at_op(self, *ops) -> bool:
        return self.tok.kind == "op" and self.tok.value in ops

    def at_word(self, *words) -> bool:
        return self.tok.kind == "word" and self.tok.value.lower() in words

    def take_op(self, op: str) -> None:
        if not self.at_op(op):
            raise Unsupported("a malformed expression",
                              "expected {0!r} at position {1}".format(op, self.tok.pos))
        self.next()

    def take_word(self, word: str) -> None:
        if not self.at_word(word):
            raise Unsupported("a malformed expression",
                              "expected {0} at position {1}".format(word.upper(), self.tok.pos))
        self.next()

    # -- grammar, loosest binding first -----------------------------------------------------------

    def parse(self) -> Any:
        value = self.or_()
        if self.tok.kind != "end":
            raise Unsupported("a malformed expression",
                              "unexpected {0!r} at position {1}".format(self.tok.value,
                                                                        self.tok.pos))
        return value

    def or_(self) -> Any:
        left = self.and_()
        if not self.at_word("or"):
            return left
        parts = [left]
        while self.at_word("or"):
            self.next()
            parts.append(self.and_())
        return ["any"] + parts

    def and_(self) -> Any:
        left = self.not_()
        if not self.at_word("and"):
            return left
        parts = [left]
        while self.at_word("and"):
            self.next()
            parts.append(self.not_())
        return ["all"] + parts

    def not_(self) -> Any:
        if self.at_word("not"):
            self.next()
            return ["!", self.not_()]
        return self.comparison()

    def comparison(self) -> Any:
        left = self.concat()

        if self.at_word("is"):
            self.next()
            negate = False
            if self.at_word("not"):
                self.next()
                negate = True
            self.take_word("null")
            return _is_null(left, negate)

        negate = False
        if self.at_word("not"):
            # NOT binds to the operator that follows: `NOT IN`, `NOT LIKE`, `NOT BETWEEN`.
            self.next()
            negate = True
            if not self.at_word("in", "like", "ilike", "between"):
                raise Unsupported("NOT", "expected IN, LIKE, ILIKE or BETWEEN after NOT")

        if self.at_word("in"):
            self.next()
            values = self.value_list()
            out = ["in", left, ["literal", values]]
            return ["!", out] if negate else out

        if self.at_word("between"):
            self.next()
            lo = self.concat()
            self.take_word("and")
            hi = self.concat()
            out = ["all", [">=", left, lo], ["<=", left, hi]]
            return ["!", out] if negate else out

        if self.at_word("like", "ilike"):
            insensitive = self.tok.value.lower() == "ilike"
            self.next()
            pattern = self.next()
            if pattern.kind != "str":
                raise Unsupported("LIKE", "the pattern must be a literal string")
            out = _like(left, pattern.value, insensitive)
            return ["!", out] if negate else out

        if self.at_op("=", "==", "<>", "!=", "<", "<=", ">", ">="):
            op = self.next().value
            right = self.concat()
            spelled = {"=": "==", "<>": "!="}.get(op, op)
            return [spelled, left, right]

        return left

    def value_list(self) -> List[Any]:
        """The `(…)` after IN. Literals only — MapLibre's `in` takes a literal array."""
        self.take_op("(")
        values = []
        while True:
            t = self.next()
            if t.kind == "str":
                values.append(t.value)
            elif t.kind == "number":
                values.append(t.value)
            elif t.kind == "word" and t.value.lower() in ("true", "false"):
                values.append(t.value.lower() == "true")
            else:
                raise Unsupported("IN", "only literal values are supported in an IN list")
            if self.at_op(","):
                self.next()
                continue
            self.take_op(")")
            return values

    def concat(self) -> Any:
        left = self.additive()
        if not self.at_op("||"):
            return left
        parts = [left]
        while self.at_op("||"):
            self.next()
            parts.append(self.additive())
        return ["concat"] + parts

    def additive(self) -> Any:
        left = self.multiplicative()
        while self.at_op("+", "-"):
            op = self.next().value
            left = [op, left, self.multiplicative()]
        return left

    def multiplicative(self) -> Any:
        left = self.unary()
        while self.at_op("*", "/", "%"):
            op = self.next().value
            left = [op, left, self.unary()]
        return left

    def unary(self) -> Any:
        if self.at_op("-"):
            self.next()
            operand = self.unary()
            # A negative literal stays a literal; anything else becomes 0 - x, which MapLibre has.
            if isinstance(operand, (int, float)) and not isinstance(operand, bool):
                return -operand
            return ["-", 0, operand]
        if self.at_op("+"):
            self.next()
            return self.unary()
        return self.primary()

    def primary(self) -> Any:
        t = self.tok

        if t.kind == "op" and t.value == "(":
            self.next()
            inner = self.or_()
            self.take_op(")")
            return inner

        if t.kind == "str":
            self.next()
            return t.value

        if t.kind == "number":
            self.next()
            return t.value

        if t.kind == "field":
            self.next()
            return ["get", t.value]

        if t.kind == "word":
            word = t.value
            low = word.lower()

            if low == "case":
                return self.case_()
            if low == "true":
                self.next()
                return True
            if low == "false":
                self.next()
                return False
            if low == "null":
                self.next()
                return None
            if word == "$id":
                self.next()
                return ["id"]
            if word.startswith("$"):
                raise Unsupported(word, "only $id has a MapLibre equivalent")
            if low in _KEYWORDS:
                raise Unsupported(word.upper(), "unexpected keyword at position {0}".format(t.pos))

            self.next()
            if self.at_op("("):
                return self.call(low, word)
            # A BARE WORD IS A FIELD. QGIS resolves an unquoted identifier against the layer's
            # attributes, and that is how most hand-written rules are spelled ("type" and type mean
            # the same thing there).
            return ["get", word]

        raise Unsupported("a malformed expression",
                          "unexpected {0!r} at position {1}".format(t.value, t.pos))

    def call(self, low: str, original: str) -> Any:
        args = self.arguments()

        if low == "if":
            if len(args) != 3:
                raise Unsupported("if()", "expects exactly three arguments")
            return ["case", args[0], args[1], args[2]]

        if low == "strpos":
            # QGIS strpos is 1-BASED and returns 0 when absent; index-of is 0-based, -1 when absent.
            # +1 makes both agree on every input, which is the only version worth shipping.
            if len(args) != 2:
                raise Unsupported("strpos()", "expects exactly two arguments")
            return ["+", ["index-of", args[1], args[0]], 1]

        if low == "substr":
            if len(args) not in (2, 3):
                raise Unsupported("substr()", "expects two or three arguments")
            start = ["-", args[1], 1] if not isinstance(args[1], int) else args[1] - 1
            if len(args) == 2:
                return ["slice", args[0], start]
            end = ["+", start, args[2]] if not (isinstance(start, int)
                                                and isinstance(args[2], int)) else start + args[2]
            return ["slice", args[0], start, end]

        if low == "left":
            if len(args) != 2:
                raise Unsupported("left()", "expects exactly two arguments")
            return ["slice", args[0], 0, args[1]]

        if low == "right":
            if len(args) != 2:
                raise Unsupported("right()", "expects exactly two arguments")
            # From the end: MapLibre slice takes negative indices the way Python does.
            return ["slice", args[0], ["-", 0, args[1]]]

        if low == "replace":
            # QGIS replace(string, before, after) has no MapLibre equivalent at all — there is no
            # string-substitution operator. Named explicitly so the message says which function.
            raise Unsupported("replace()", "MapLibre has no string substitution")

        spec = _FUNCTIONS.get(low)
        if spec is None:
            raise Unsupported("{0}()".format(original))
        op, lo, hi = spec
        if len(args) < lo or (hi is not None and len(args) > hi):
            raise Unsupported("{0}()".format(original),
                              "expects {0}{1} argument(s)".format(
                                  lo, "" if hi == lo else " or more" if hi is None
                                  else " to {0}".format(hi)))
        return [op] + args

    def arguments(self) -> List[Any]:
        self.take_op("(")
        if self.at_op(")"):
            self.next()
            return []
        args = [self.or_()]
        while self.at_op(","):
            self.next()
            args.append(self.or_())
        self.take_op(")")
        return args

    def case_(self) -> Any:
        """`CASE WHEN a THEN b [WHEN …] [ELSE c] END` → `["case", a, b, …, c]`.

        MapLibre's `case` REQUIRES a fallback, where QGIS's ELSE is optional. Omitting it there
        yields NULL, so that is what a missing ELSE becomes here — not an invented default, which
        would draw features the author chose to leave undrawn.
        """
        self.take_word("case")
        parts: List[Any] = ["case"]
        if not self.at_word("when"):
            raise Unsupported("CASE", "expected WHEN")
        while self.at_word("when"):
            self.next()
            condition = self.or_()
            self.take_word("then")
            parts.append(condition)
            parts.append(self.or_())
        if self.at_word("else"):
            self.next()
            parts.append(self.or_())
        else:
            parts.append(None)
        self.take_word("end")
        return parts


# ── Pieces with a decision in them ───────────────────────────────────────────────────────────────

def _is_null(operand: Any, negate: bool) -> Any:
    """`IS NULL` / `IS NOT NULL`.

    ABSENCE AND NULL ARE THE SAME THING IN A VECTOR TILE — an attribute with no value is simply not
    encoded — so `has` is the honest test there and `["==", x, null]` would be false for the very
    features the author meant. On a GeoJSON source a property CAN be present and null, so both are
    tested. `has` needs a property NAME, which is why a non-field operand falls back to the
    comparison alone.
    """
    if isinstance(operand, list) and len(operand) == 2 and operand[0] == "get" \
            and isinstance(operand[1], str):
        name = operand[1]
        missing = ["any", ["!", ["has", name]], ["==", ["get", name], None]]
        return ["!", missing] if negate else missing
    return ["!=", operand, None] if negate else ["==", operand, None]


def _like(operand: Any, pattern: str, insensitive: bool) -> Any:
    """SQL `LIKE`, for the three patterns a web map can actually answer.

    `%` is the only wildcard handled, and only at the ends: `abc%` (starts with), `%abc` (ends
    with), `%abc%` (contains) and a bare `abc` (equals). A `%` or `_` in the MIDDLE would need a
    regular expression, which MapLibre does not have — raising there is the point of this module.
    """
    if "_" in pattern:
        raise Unsupported("LIKE", "the single-character wildcard _ has no MapLibre equivalent")
    body = pattern.strip("%")
    if "%" in body:
        raise Unsupported("LIKE", "a % in the middle of a pattern needs a regular expression")
    subject = ["downcase", operand] if insensitive else operand
    needle = body.lower() if insensitive else body
    starts, ends = pattern.startswith("%"), pattern.endswith("%")
    if starts and ends:
        return ["!=", ["index-of", needle, subject], -1]
    if ends:                                    # 'abc%' — starts with
        return ["==", ["slice", subject, 0, len(needle)], needle]
    if starts:                                  # '%abc' — ends with
        return ["==", ["slice", subject, -len(needle)], needle] if needle else True
    return ["==", subject, needle]


# ── The public surface ───────────────────────────────────────────────────────────────────────────

def to_maplibre(expression: str) -> Any:
    """A QGIS expression as a MapLibre expression. Raises `Unsupported` outside the subset.

    An empty expression is `None` rather than `True`: "no filter" and "a filter that matches
    everything" are the same on a map but not in a style, and MapLibre wants the key absent.
    """
    text = (expression or "").strip()
    if not text:
        return None
    return _Parser(tokenize(text)).parse()


def try_maplibre(expression: str) -> Tuple[Optional[Any], Optional[str]]:
    """`(filter, None)` when it translates, `(None, reason)` when it does not.

    The shape a fidelity report wants: a caller listing what will and will not travel should not have
    to wrap every rule in its own try/except.
    """
    try:
        return to_maplibre(expression), None
    except Unsupported as exc:
        return None, str(exc)
    except Exception as exc:                    # noqa: BLE001 - a parser bug must not stop a push
        return None, "could not be read ({0}: {1})".format(type(exc).__name__, exc)


def supports(expression: str) -> bool:
    """True when `expression` translates. For a UI that wants to grey something out."""
    return try_maplibre(expression)[1] is None


def negate(filters: Sequence[Any]) -> Any:
    """The MapLibre filter matching everything `filters` does not — how an ELSE rule is expressed.

    QGIS's ELSE rule draws the features no sibling rule matched, and MapLibre has no such concept:
    every layer stands alone with its own filter. So the ELSE becomes NOT(any of the siblings),
    which is the same set as long as the siblings' filters are the ones actually emitted.
    """
    real = [f for f in filters if f is not None]
    if not real:
        return None                             # every sibling matched everything: ELSE is empty
    return ["!", real[0] if len(real) == 1 else ["any"] + list(real)]


# ── Back the other way ───────────────────────────────────────────────────────────────────────────

#: MapLibre operator → QGIS operator, for the comparisons this module emits.
_BACK_COMPARISON = {"==": "=", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}


def from_maplibre(expression: Any) -> str:
    """A MapLibre expression as a QGIS one. Raises `Unsupported` outside what `to_maplibre` emits.

    WHY THIS IS NARROWER THAN ITS TWIN, ON PURPOSE. Going out, the input is whatever a person typed
    in QGIS, so the subset has to be generous. Coming back, the input is a filter GeoDeploy itself
    stored — either translated by `to_maplibre` or built by the portal editor — so the shapes are
    known, and anything else is a filter we did not write. Guessing at those is how a rule quietly
    changes which features it draws.

    A rule that arrived FROM QGIS also carries its original expression text, which is used in
    preference to this: a round trip should hand back exactly what it was given, not a
    re-rendering of it. This exists for the other case — a rule authored in GeoDeploy, opened in
    QGIS for the first time.
    """
    return _back(expression)


def _back(node: Any) -> str:
    if node is None:
        return "NULL"
    if node is True:
        return "TRUE"
    if node is False:
        return "FALSE"
    if isinstance(node, (int, float)):
        return repr(node)
    if isinstance(node, str):
        return "'" + node.replace("'", "''") + "'"
    if not isinstance(node, list) or not node:
        raise Unsupported(repr(node), "not a MapLibre expression")

    op, args = node[0], node[1:]

    if op == "get" and len(args) == 1 and isinstance(args[0], str):
        return '"' + args[0].replace('"', '""') + '"'
    if op == "id" and not args:
        return "$id"
    if op == "literal" and len(args) == 1:
        raise Unsupported("literal", "a bare literal array has no QGIS equivalent outside IN")
    if op == "has" and len(args) == 1 and isinstance(args[0], str):
        return '"{0}" IS NOT NULL'.format(args[0].replace('"', '""'))

    if op in _BACK_COMPARISON and len(args) == 2:
        return "{0} {1} {2}".format(_back(args[0]), _BACK_COMPARISON[op], _back(args[1]))

    if op == "all":
        return _join(args, "AND")
    if op == "any":
        # The `IS NULL` shape this module emits round-trips as itself rather than as its parts.
        null_field = _null_field(node)
        if null_field is not None:
            return '"{0}" IS NULL'.format(null_field.replace('"', '""'))
        return _join(args, "OR")

    if op == "!" and len(args) == 1:
        inner = args[0]
        null_field = _null_field(inner)
        if null_field is not None:
            return '"{0}" IS NOT NULL'.format(null_field.replace('"', '""'))
        if isinstance(inner, list) and inner and inner[0] == "in" and len(inner) == 3:
            return _in(inner, negated=True)
        return "NOT ({0})".format(_back(inner))

    if op == "in" and len(args) == 2:
        return _in(node, negated=False)

    if op in ("+", "-", "*", "/", "%") and len(args) == 2:
        return "({0} {1} {2})".format(_back(args[0]), op, _back(args[1]))
    if op == "concat":
        return " || ".join(_back(a) for a in args)
    if op == "downcase" and len(args) == 1:
        return "lower({0})".format(_back(args[0]))
    if op == "upcase" and len(args) == 1:
        return "upper({0})".format(_back(args[0]))
    if op == "coalesce":
        return "coalesce({0})".format(", ".join(_back(a) for a in args))

    raise Unsupported(str(op), "no QGIS equivalent for this MapLibre operator")


def _join(args: Sequence[Any], keyword: str) -> str:
    parts = []
    for a in args:
        text = _back(a)
        # Parenthesise anything that is itself a combination, or precedence rewrites the filter.
        parts.append("({0})".format(text) if isinstance(a, list) and a and a[0] in
                     ("all", "any") else text)
    return (" " + keyword + " ").join(parts)


def _in(node: Sequence[Any], negated: bool) -> str:
    subject, container = node[1], node[2]
    if not (isinstance(container, list) and len(container) == 2 and container[0] == "literal"
            and isinstance(container[1], list)):
        raise Unsupported("in", "expected a literal array")
    values = ", ".join(_back(v) for v in container[1])
    return "{0}{1} IN ({2})".format(_back(subject), " NOT" if negated else "", values)


def _null_field(node: Any):
    """The field name when `node` is the `IS NULL` shape `_is_null` emits, else None."""
    if not (isinstance(node, list) and len(node) == 3 and node[0] == "any"):
        return None
    absent, is_null = node[1], node[2]
    if not (isinstance(absent, list) and len(absent) == 2 and absent[0] == "!"):
        return None
    has = absent[1]
    if not (isinstance(has, list) and len(has) == 2 and has[0] == "has"):
        return None
    if not (isinstance(is_null, list) and len(is_null) == 3 and is_null[0] == "=="
            and is_null[2] is None):
        return None
    return has[1]
