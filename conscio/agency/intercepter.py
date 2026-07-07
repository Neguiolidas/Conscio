# conscio/agency/intercepter.py
"""Intercepter — safe AST evaluator for [INTERCEPT: ...] tags in LLM output.

Origin: Think-Vetor DSL concept (CromIA). Reimplemented from scratch.
License: AGPL-3.0 (pending — see docs/superpowers/specs/2026-07-06-intercepter-spec.md)
"""
from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .adapter import InferenceAdapter, InferenceResult


_TAG_PREFIX = "[INTERCEPT:"
_MAX_EXPR_LEN = 500
_MAX_DEPTH = 50


@dataclass(frozen=True)
class InterceptResult:
    text: str
    intercepted: bool
    count: int
    errors: list[str]


def _scan_tags(text: str) -> list[tuple[int, int, str]]:
    """Find all [INTERCEPT: ...] tags by bracket counting.

    Returns list of (start, end, expression) tuples.  Handles arbitrary
    nesting depth via a depth counter rather than a regex.
    """
    results: list[tuple[int, int, str]] = []
    i = 0
    while i < len(text):
        pos = text.find(_TAG_PREFIX, i)
        if pos == -1:
            break
        depth = 0
        j = pos + len(_TAG_PREFIX)
        while j < len(text):
            ch = text[j]
            if ch == "[":
                depth += 1
            elif ch == "]":
                if depth == 0:
                    expr = text[pos + len(_TAG_PREFIX) : j].strip()
                    # Include even empty expressions so process() can emit
                    # a proper [ERROR: empty expression] instead of silently
                    # passing the tag through as plain text.
                    results.append((pos, j + 1, expr))
                    break
                depth -= 1
            j += 1
        i = (j + 1) if j < len(text) else len(text)
    return results


class InterceptError(Exception):
    """Raised for any Intercepter evaluation failure."""


class Intercepter:
    """Safe AST evaluator for [INTERCEPT: expr] tags."""

    _ALLOWED_OPS: dict[type, Any] = {}  # populated after class body

    _ALLOWED_FUNCS: dict[str, Callable[..., Any]] = {
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sum": sum,
        "pow": pow,
        "len": len,
    }

    _DANGEROUS_MODULES = frozenset({
        "os", "posix", "subprocess", "_posixsubprocess", "socket", "http",
        "urllib", "shutil", "sys", "importlib", "ctypes", "builtins",
    })
    _ALLOWED_PARAM_TYPES = frozenset({int, float, str, bool})
    _ALLOWED_RETURN_TYPES = frozenset({int, float, str, bool})

    def __init__(self) -> None:
        self._functions: dict[str, Callable[..., Any]] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        defaults: dict[str, Callable[..., Any]] = {
            "sqrt": math.sqrt,
            "floor": math.floor,
            "ceil": math.ceil,
            "log": math.log,
            "sin": math.sin,
            "cos": math.cos,
            "tan": math.tan,
        }
        for name, fn in defaults.items():
            self._functions[name] = fn

    def register_function(self, name: str, fn: Callable[..., Any]) -> None:
        """Register a custom function for use in [INTERCEPT: ...] tags.

        SECURITY: Only register functions you trust. This grants the LLM
        the ability to call this function. The function must:

        - Not be from a dangerous module (os, subprocess, socket, etc.)
        - Have all parameters annotated as int, float, str, or bool
        - Return int, float, str, or bool
        - Not shadow a Python builtin or an existing registered function
        """
        import inspect

        # Layer 1: name collision
        if name in self._ALLOWED_FUNCS:
            raise ValueError(f"name '{name}' shadows a builtin")
        if name in self._functions:
            raise ValueError(f"name '{name}' already registered")

        # Layer 2: module blocklist (check BEFORE annotations — dangerous
        # modules must be rejected regardless of signature)
        mod = getattr(fn, "__module__", "") or ""
        if mod.split(".")[0] in self._DANGEROUS_MODULES:
            raise ValueError(
                f"Cannot register function from dangerous module '{mod}'."
            )

        # Layer 3: type annotation check
        sig = inspect.signature(fn)
        for param in sig.parameters.values():
            ann = param.annotation
            if ann is inspect.Parameter.empty:
                raise ValueError(
                    f"Parameter '{param.name}' has no type annotation. "
                    f"All parameters must be annotated as int, float, str, or bool."
                )
            if ann not in self._ALLOWED_PARAM_TYPES:
                raise ValueError(
                    f"Parameter '{param.name}' has type '{ann}', "
                    f"which is not allowed. Only int, float, str, bool."
                )

        # Layer 4: dry-run return type check
        try:
            dummy_args = {
                p.name: (0 if p.annotation in (int, float)
                         else False if p.annotation is bool else "")
                for p in sig.parameters.values()
            }
            result = fn(**dummy_args)
            if not isinstance(result, tuple(self._ALLOWED_RETURN_TYPES)):
                raise ValueError(
                    f"Function returned {type(result).__name__}, "
                    f"expected int, float, str, or bool."
                )
        except ValueError:
            raise  # re-raise our own
        except Exception:
            pass  # dry-run failure is OK (e.g. sqrt(-1) raises)

        self._functions[name] = fn

    # ── public API ──

    def process(self, text: str) -> InterceptResult:
        """Find all [INTERCEPT: ...] tags, evaluate, and inline results."""
        tags = _scan_tags(text)
        if not tags:
            return InterceptResult(
                text=text, intercepted=False, count=0, errors=[]
            )

        # Build the result by replacing tags from RIGHT to LEFT so that
        # earlier positions remain valid (avoid offset drift).
        result_text = text
        errors: list[str] = []
        count = 0

        for start, end, expr in reversed(tags):
            count += 1
            # original tag text, replaced positionally (right-to-left)
            try:
                value = self._eval(expr)
                replacement = f"[INTERCEPT: {expr}] -> [RESULT: {value}]"
                errors.append("")
            except InterceptError as exc:
                replacement = f"[INTERCEPT: {expr}] -> [ERROR: {exc}]"
                errors.append(str(exc))
            except Exception as exc:
                replacement = f"[INTERCEPT: {expr}] -> [ERROR: {exc}]"
                errors.append(str(exc))

            result_text = result_text[:start] + replacement + result_text[end:]

        # errors were collected in reverse order; un-reverse them
        errors.reverse()
        count = len(tags)

        return InterceptResult(
            text=result_text, intercepted=True, count=count, errors=errors
        )

    # ── AST evaluator ──

    def _eval(self, expr: str) -> Any:
        if len(expr) > _MAX_EXPR_LEN:
            raise InterceptError("expression too long")
        if not expr.strip():
            raise InterceptError("empty expression")
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as exc:
            raise InterceptError(f"syntax error: {exc.msg}")
        return self._eval_node(tree.body, depth=0)

    def _eval_node(self, node: ast.AST, depth: int) -> Any:
        if depth > _MAX_DEPTH:
            raise InterceptError("recursion limit exceeded")

        # Literals
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, bool)):
                return node.value
            raise InterceptError(
                f"unsupported constant: {type(node.value).__name__}"
            )

        # Binary ops
        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in self._ALLOWED_OPS:
                raise InterceptError(
                    f"operator {op_type.__name__} not allowed"
                )
            left = self._eval_node(node.left, depth + 1)
            right = self._eval_node(node.right, depth + 1)
            try:
                return self._ALLOWED_OPS[op_type](left, right)
            except ZeroDivisionError:
                raise InterceptError("division by zero")

        # Unary ops
        if isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in self._ALLOWED_OPS:
                raise InterceptError(
                    f"unary operator {op_type.__name__} not allowed"
                )
            operand = self._eval_node(node.operand, depth + 1)
            return self._ALLOWED_OPS[op_type](operand)

        # Function calls
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise InterceptError("only named functions allowed")
            fn_name = node.func.id
            if node.keywords:
                raise InterceptError("keyword arguments not supported")
            fn = self._functions.get(fn_name) or self._ALLOWED_FUNCS.get(fn_name)
            if fn is None:
                raise InterceptError(f"unknown function: {fn_name}")
            args = [self._eval_node(a, depth + 1) for a in node.args]
            try:
                return fn(*args)
            except (ValueError, TypeError) as exc:
                raise InterceptError(str(exc))

        # Comparisons
        if isinstance(node, ast.Compare):
            return self._eval_compare(node, depth)

        # Security blocks — explicit rejections
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise InterceptError("import not allowed")
        if isinstance(node, ast.Attribute):
            raise InterceptError("attribute access not allowed")
        if isinstance(node, ast.Subscript):
            raise InterceptError("subscript not allowed")
        if isinstance(node, (ast.List, ast.Tuple)):
            raise InterceptError("list/tuple literals not supported")
        if isinstance(node, ast.Dict):
            raise InterceptError("dict literals not supported")
        if isinstance(node, ast.Set):
            raise InterceptError("set literals not supported")
        if isinstance(node, ast.Name):
            raise InterceptError(
                f"variable '{node.id}' not supported — stateless evaluation"
            )
        if isinstance(node, ast.BoolOp):
            raise InterceptError("boolean operators not supported")
        if isinstance(node, ast.IfExp):
            raise InterceptError("conditional expressions not supported")

        raise InterceptError(f"unsupported node: {type(node).__name__}")

    def _eval_compare(self, node: ast.Compare, depth: int) -> Any:
        left = self._eval_node(node.left, depth + 1)
        result = True
        for op, comp in zip(node.ops, node.comparators):
            right = self._eval_node(comp, depth + 1)
            op_type = type(op)
            if op_type == ast.Gt:
                result = left > right
            elif op_type == ast.Lt:
                result = left < right
            elif op_type == ast.GtE:
                result = left >= right
            elif op_type == ast.LtE:
                result = left <= right
            elif op_type == ast.Eq:
                result = left == right
            elif op_type == ast.NotEq:
                result = left != right
            else:
                raise InterceptError(
                    f"comparison {op_type.__name__} not allowed"
                )
            if not result:
                return False
            left = right
        return result


# Populate _ALLOWED_OPS after class definition (refs methods of math etc.)
Intercepter._ALLOWED_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a ** b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.USub: lambda a: -a,
    ast.UAdd: lambda a: +a,
}


# ── InterceptionLoop ────────────────────────────────────────────────────

_MAX_OBSERVATION_CHARS = 2000


class InterceptionLoop:
    """Orchestrates generate→intercept→reinject up to *max_iterations*.

    Drop-in replacement for ``adapter.generate()`` — returns the same
    ``InferenceResult`` type.  When no tags are found the loop exits after a
    single generate call (the common path).
    """

    def __init__(
        self,
        adapter: "InferenceAdapter",  # noqa: F821 — string ref to adapter.InferenceAdapter
        intercepter: Intercepter,
        max_iterations: int = 3,
        emit_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.adapter = adapter
        self.intercepter = intercepter
        self.max_iterations = max_iterations
        self.emit_fn = emit_fn

    def generate(
        self,
        prompt: str,
        *,
        schema: dict | None = None,
        grammar: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        stop: list[str] | None = None,
    ) -> "InferenceResult":  # noqa: F821
        observations: list[str] = []
        total_tokens_in = 0
        total_tokens_out = 0
        total_latency = 0
        intercept_result: InterceptResult | None = None

        for i in range(self.max_iterations):
            full_prompt = prompt
            if observations:
                obs_block = "\n".join(observations)
                if len(obs_block) > _MAX_OBSERVATION_CHARS:
                    obs_block = obs_block[-_MAX_OBSERVATION_CHARS:]
                full_prompt = (
                    f"{prompt}\n<observation>\n{obs_block}\n</observation>"
                )

            result = self.adapter.generate(
                full_prompt,
                schema=schema,
                grammar=grammar,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
            )
            total_tokens_in += result.tokens_in
            total_tokens_out += result.tokens_out
            total_latency += result.latency_ms

            intercept_result = self.intercepter.process(result.text)

            if self.emit_fn:
                self.emit_fn(
                    type="tool_call",
                    category="external",
                    data={
                        "action": "intercept",
                        "iteration": i,
                        "count": intercept_result.count,
                        "errors": [e for e in intercept_result.errors if e],
                    },
                )

            if not intercept_result.intercepted:
                # Clean text — return immediately (common path).
                return result

            observations.append(intercept_result.text)

        # Max iterations reached — return the adapter's last result text,
        # but with cumulative metering.  We cannot reuse ``result`` directly
        # because its metering fields are per-call; build a fresh one.
        from .adapter import InferenceResult  # local import — avoid cycle at module load
        return InferenceResult(
            text=intercept_result.text if intercept_result else "",
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            latency_ms=total_latency,
        )

