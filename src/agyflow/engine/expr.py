"""Recursive descent evaluator for when/until expressions without eval or exec."""

from __future__ import annotations

import re
from typing import Any

TOKEN_RE = re.compile(
    r"\s*(?:(==|!=|<=|>=|<|>)"
    r"|(\b(?:or|and|not|true|false|null|len|exists)\b)"
    r"|(\d+(?:\.\d+)?)"
    r"|'([^']*)'"
    r"|([a-zA-Z0-9_\-\.]+\[\d+\][a-zA-Z0-9_\-\.\[\]]*|[a-zA-Z0-9_\-\.]+)"
    r"|(\(|\)))",
    re.IGNORECASE,
)


def _resolve_ref(path: str, context: Any) -> Any:
    """Resolve a dot/bracket notation reference against a context dictionary or object."""
    if not path or context is None:
        return None
    # Support method or mapping resolution on context
    lookup_fn = getattr(context, "resolve_ref_value", None)
    if callable(lookup_fn):
        return lookup_fn(path)

    parts = re.split(r"\.|(?=\[\d+\])", path)
    curr: Any = context
    for raw_p in parts:
        if not raw_p or curr is None:
            continue
        if raw_p.startswith("[") and raw_p.endswith("]"):
            try:
                idx = int(raw_p[1:-1])
                curr = curr[idx] if isinstance(curr, (list, tuple)) and 0 <= idx < len(curr) else None
            except (ValueError, IndexError, TypeError):
                return None
        else:
            if isinstance(curr, dict):
                curr = curr.get(raw_p)
            elif hasattr(curr, raw_p):
                curr = getattr(curr, raw_p)
            else:
                return None
    return curr


class ExprParser:
    """Recursive descent expression parser for agyflow when/until grammar."""

    def __init__(self, expr_str: str, context: Any) -> None:
        self.expr_str = expr_str.strip()
        self.context = context
        self.tokens: list[tuple[str, Any]] = self._tokenize(self.expr_str)
        self.pos = 0

    def _tokenize(self, s: str) -> list[tuple[str, Any]]:
        tokens: list[tuple[str, Any]] = []
        for match in TOKEN_RE.finditer(s):
            op, kw, num, str_val, ident, paren = match.groups()
            if op:
                tokens.append(("OP", op))
            elif kw:
                lkw = kw.lower()
                if lkw in ("true", "false", "null"):
                    val = True if lkw == "true" else (False if lkw == "false" else None)
                    tokens.append(("LITERAL", val))
                else:
                    tokens.append((lkw.upper(), lkw))
            elif num is not None:
                val = float(num) if "." in num else int(num)
                tokens.append(("LITERAL", val))
            elif str_val is not None:
                tokens.append(("LITERAL", str_val))
            elif ident is not None:
                tokens.append(("IDENT", ident))
            elif paren:
                tokens.append(("PAREN", paren))
        return tokens

    def _peek(self) -> tuple[str, Any] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _consume(self, expected_type: str | None = None) -> tuple[str, Any]:
        if self.pos >= len(self.tokens):
            raise ValueError(f"Unexpected end of expression: {self.expr_str}")
        tok = self.tokens[self.pos]
        if expected_type and tok[0] != expected_type:
            raise ValueError(f"Expected {expected_type} but got {tok} in: {self.expr_str}")
        self.pos += 1
        return tok

    def parse(self) -> Any:
        if not self.tokens:
            return True
        res = self._parse_or()
        if self.pos < len(self.tokens):
            raise ValueError(f"Unexpected trailing tokens: {self.tokens[self.pos:]}")
        return res

    def _parse_or(self) -> Any:
        left = self._parse_and()
        while (p := self._peek()) and p[0] == "OR":
            self._consume("OR")
            right = self._parse_and()
            left = bool(left) or bool(right)
        return left

    def _parse_and(self) -> Any:
        left = self._parse_not()
        while (p := self._peek()) and p[0] == "AND":
            self._consume("AND")
            right = self._parse_not()
            left = bool(left) and bool(right)
        return left

    def _parse_not(self) -> Any:
        if (p := self._peek()) and p[0] == "NOT":
            self._consume("NOT")
            return not bool(self._parse_not())
        return self._parse_cmp()

    def _parse_cmp(self) -> Any:
        left = self._parse_operand()
        if (p := self._peek()) and p[0] == "OP":
            op = self._consume("OP")[1]
            right = self._parse_operand()
            return self._eval_cmp(op, left, right)
        return left

    def _eval_cmp(self, op: str, a: Any, b: Any) -> bool:
        if a is None or b is None:
            if op == "==":
                return a is b
            if op == "!=":
                return a is not b
            return False
        if op == "==":
            return bool(a == b)
        if op == "!=":
            return bool(a != b)
        if op == "<":
            return bool(a < b)
        if op == "<=":
            return bool(a <= b)
        if op == ">":
            return bool(a > b)
        if op == ">=":
            return bool(a >= b)
        raise ValueError(f"Unknown operator: {op}")

    def _parse_operand(self) -> Any:
        p = self._peek()
        if not p:
            raise ValueError(f"Premature end of expression: {self.expr_str}")
        if p[0] == "PAREN" and p[1] == "(":
            self._consume("PAREN")
            val = self._parse_or()
            self._consume("PAREN")
            return val
        if p[0] == "LEN":
            self._consume("LEN")
            self._consume("PAREN")
            ref_tok = self._consume("IDENT")
            self._consume("PAREN")
            val = _resolve_ref(ref_tok[1], self.context)
            return len(val) if isinstance(val, (list, dict, str, tuple, set)) else 0
        if p[0] == "EXISTS":
            self._consume("EXISTS")
            self._consume("PAREN")
            ref_tok = self._consume("IDENT")
            self._consume("PAREN")
            return _resolve_ref(ref_tok[1], self.context) is not None
        if p[0] == "LITERAL":
            return self._consume("LITERAL")[1]
        if p[0] == "IDENT":
            ref = self._consume("IDENT")[1]
            return _resolve_ref(ref, self.context)
        raise ValueError(f"Unexpected token {p} in: {self.expr_str}")


def eval_expr(expr_str: str | None, context: Any) -> bool:
    """Evaluate a boolean expression string against context. None or empty returns True."""
    if expr_str is None or not str(expr_str).strip():
        return True
    return bool(ExprParser(str(expr_str), context).parse())
