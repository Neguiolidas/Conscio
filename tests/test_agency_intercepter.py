# tests/test_agency_intercepter.py
"""Tests for Intercepter: safe AST evaluator for [INTERCEPT: ...] tags.

Origin: Think-Vetor DSL concept (CromIA). Reimplemented from scratch.
"""
import os
import pytest

from conscio.agency.intercepter import (
    Intercepter,
)


# ── Task 1: tag scanner + empty text ──

class TestScanTags:
    def test_empty_text(self):
        result = Intercepter().process("")
        assert result.text == ""
        assert result.intercepted is False
        assert result.count == 0
        assert result.errors == []

    def test_no_tags(self):
        result = Intercepter().process("Hello world, no tags here.")
        assert result.text == "Hello world, no tags here."
        assert result.intercepted is False
        assert result.count == 0


# ── Task 2: arithmetic operators ──

class TestArithmetic:
    def test_single_arithmetic(self):
        r = Intercepter().process("[INTERCEPT: 2+2]")
        assert "[RESULT: 4]" in r.text
        assert r.intercepted is True
        assert r.count == 1

    def test_subtraction(self):
        r = Intercepter().process("[INTERCEPT: 10-3]")
        assert "[RESULT: 7]" in r.text

    def test_multiplication(self):
        r = Intercepter().process("[INTERCEPT: 6*7]")
        assert "[RESULT: 42]" in r.text

    def test_division(self):
        r = Intercepter().process("[INTERCEPT: 20/4]")
        assert "[RESULT: 5.0]" in r.text

    def test_power(self):
        r = Intercepter().process("[INTERCEPT: 2**10]")
        assert "[RESULT: 1024]" in r.text

    def test_floor_div(self):
        r = Intercepter().process("[INTERCEPT: 17//5]")
        assert "[RESULT: 3]" in r.text

    def test_modulo(self):
        r = Intercepter().process("[INTERCEPT: 17%5]")
        assert "[RESULT: 2]" in r.text

    def test_nested_parens(self):
        r = Intercepter().process("[INTERCEPT: (2+3)*4]")
        assert "[RESULT: 20]" in r.text

    def test_unary_minus(self):
        r = Intercepter().process("[INTERCEPT: -5+10]")
        assert "[RESULT: 5]" in r.text

    def test_float_result(self):
        r = Intercepter().process("[INTERCEPT: 10/3]")
        assert "[RESULT: 3.333" in r.text


# ── Task 3: security guards ──

class TestSecurity:
    def test_no_eval(self):
        r = Intercepter().process("[INTERCEPT: __import__('os')]")
        assert "[ERROR:" in r.text
        assert r.intercepted is True

    def test_no_attribute_access(self):
        r = Intercepter().process("[INTERCEPT: (1).__class__]")
        assert "[ERROR:" in r.text

    def test_no_subscript(self):
        r = Intercepter().process("[INTERCEPT: [1,2][0]]")
        assert "[ERROR:" in r.text

    def test_no_list_literal(self):
        r = Intercepter().process("[INTERCEPT: [1,2,3]]")
        assert "[ERROR:" in r.text

    def test_no_dict_literal(self):
        r = Intercepter().process("[INTERCEPT: {'a': 1}]")
        assert "[ERROR:" in r.text

    def test_size_limit(self):
        expr = "1" + "+" + "1" * 600
        r = Intercepter().process(f"[INTERCEPT: {expr}]")
        assert "[ERROR: expression too long]" in r.text

    def test_depth_limit(self):
        # Build a deeply nested expression -- each +1 creates a BinOp node
        # so depth grows linearly.  60 levels exceeds _MAX_DEPTH (50).
        expr = "1"
        for _ in range(60):
            expr = f"({expr}+1)"
        r = Intercepter().process(f"[INTERCEPT: {expr}]")
        assert "[ERROR:" in r.text

    def test_malformed_tag(self):
        r = Intercepter().process("[INTERCEPT: 2+]")
        assert "[ERROR:" in r.text

    def test_empty_expression(self):
        r = Intercepter().process("[INTERCEPT: ]")
        assert "[ERROR: empty expression]" in r.text

    def test_unknown_function(self):
        r = Intercepter().process("[INTERCEPT: factorial(5)]")
        assert "[ERROR: unknown function: factorial]" in r.text


# ── Task 4: functions, multiple tags, errors ──

class TestFunctions:
    def test_abs(self):
        r = Intercepter().process("[INTERCEPT: abs(-5)]")
        assert "[RESULT: 5]" in r.text

    def test_round(self):
        r = Intercepter().process("[INTERCEPT: round(3.14159, 2)]")
        assert "[RESULT: 3.14]" in r.text

    def test_min_max(self):
        r = Intercepter().process("[INTERCEPT: max(1,2,3)]")
        assert "[RESULT: 3]" in r.text

    def test_sqrt(self):
        r = Intercepter().process("[INTERCEPT: sqrt(144)]")
        assert "[RESULT: 12.0]" in r.text

    def test_floor(self):
        r = Intercepter().process("[INTERCEPT: floor(3.7)]")
        assert "[RESULT: 3]" in r.text

    def test_comparison(self):
        r = Intercepter().process("[INTERCEPT: 5 > 3]")
        assert "[RESULT: True]" in r.text

    def test_division_by_zero(self):
        r = Intercepter().process("[INTERCEPT: 1/0]")
        assert "[ERROR: division by zero]" in r.text

    def test_sqrt_negative(self):
        r = Intercepter().process("[INTERCEPT: sqrt(-1)]")
        assert "[ERROR:" in r.text


class TestMultipleTags:
    def test_multiple_tags(self):
        r = Intercepter().process("[INTERCEPT: 2+2] and [INTERCEPT: 3*3]")
        assert "[RESULT: 4]" in r.text
        assert "[RESULT: 9]" in r.text
        assert r.count == 2

    def test_duplicate_tags(self):
        """Same expression appearing twice — both must be resolved."""
        r = Intercepter().process("[INTERCEPT: 2+2] and [INTERCEPT: 2+2]")
        assert r.text.count("[RESULT: 4]") == 2
        assert r.count == 2

    def test_error_continues_processing(self):
        r = Intercepter().process("[INTERCEPT: 1/0] then [INTERCEPT: 2+2]")
        assert "[ERROR: division by zero]" in r.text
        assert "[RESULT: 4]" in r.text
        assert r.count == 2
        assert len([e for e in r.errors if e]) == 1

    def test_tag_not_on_own_line(self):
        r = Intercepter().process("Result: [INTERCEPT: 6*7] done.")
        assert "[RESULT: 42]" in r.text
        assert r.text.startswith("Result: ")
        assert r.text.endswith(" done.")

    def test_tag_with_nested_brackets(self):
        r = Intercepter().process("[INTERCEPT: max(1, max(2, 3))]")
        assert "[RESULT: 3]" in r.text

    def test_result_format(self):
        r = Intercepter().process("[INTERCEPT: 2+2]")
        assert "[INTERCEPT: 2+2] -> [RESULT: 4]" in r.text


# ── Task 5: register_function 4-layer guard ──

class TestRegisterFunction:
    def test_register_valid_function(self):
        inter = Intercepter()
        def double(x: float) -> float:
            return x * 2
        inter.register_function("double", double)
        r = inter.process("[INTERCEPT: double(5)]")
        assert "[RESULT: 10]" in r.text

    def test_register_dangerous_module(self):
        inter = Intercepter()
        with pytest.raises(ValueError, match="dangerous module"):
            inter.register_function("syscall", os.system)

    def test_register_missing_type_annotation(self):
        inter = Intercepter()
        with pytest.raises(ValueError, match="no type annotation"):
            inter.register_function("bad", lambda x: x * 2)

    def test_register_non_primitive_param(self):
        inter = Intercepter()
        def bad(x: list) -> int:
            return len(x)
        with pytest.raises(ValueError, match="not allowed"):
            inter.register_function("bad", bad)

    def test_register_non_primitive_return(self):
        inter = Intercepter()
        def bad_list(x: int) -> str:
            return [x]  # type annotation lies, returns list
        with pytest.raises(ValueError, match="returned"):
            inter.register_function("bad", bad_list)

    def test_register_name_collision_builtin(self):
        inter = Intercepter()
        def my_abs(x: int) -> int:
            return abs(x)
        with pytest.raises(ValueError, match="shadows"):
            inter.register_function("abs", my_abs)

    def test_register_name_collision_existing(self):
        inter = Intercepter()
        def double(x: float) -> float:
            return x * 2
        inter.register_function("double", double)
        def triple(x: float) -> float:
            return x * 3
        with pytest.raises(ValueError, match="already registered"):
            inter.register_function("double", triple)

    def test_register_dry_run_failure_ok(self):
        """Registration should succeed even if dry-run with zero fails."""
        inter = Intercepter()
        def safe_sqrt(x: float) -> float:
            if x < 0:
                raise ValueError("domain error")
            return x ** 0.5
        inter.register_function("my_sqrt", safe_sqrt)
        r = inter.process("[INTERCEPT: my_sqrt(9)]")
        assert "[RESULT: 3" in r.text


# ── Task 6: InterceptionLoop ──

from conscio.agency.adapter import MockAdapter  # noqa: E402
from conscio.agency.intercepter import InterceptionLoop  # noqa: E402


class TestInterceptionLoop:
    def test_no_tags_one_iteration(self):
        adapter = MockAdapter(script=["Hello, no tags here."])
        loop = InterceptionLoop(adapter, Intercepter(), max_iterations=3)
        result = loop.generate("test prompt")
        assert result.text == "Hello, no tags here."
        assert len(adapter.calls) == 1

    def test_one_tag_two_iterations(self):
        adapter = MockAdapter(script=[
            "[INTERCEPT: 2+2]",
            "The answer is 4.",
        ])
        loop = InterceptionLoop(adapter, Intercepter(), max_iterations=3)
        result = loop.generate("test")
        assert "4" in result.text or "RESULT: 4" in result.text
        assert len(adapter.calls) == 2

    def test_max_iterations_cap(self):
        adapter = MockAdapter(script=[
            "[INTERCEPT: 1+1]",
            "[INTERCEPT: 2+2]",
            "[INTERCEPT: 3+3]",
            "[INTERCEPT: 4+4]",
        ])
        loop = InterceptionLoop(adapter, Intercepter(), max_iterations=3)
        loop.generate("test")
        assert len(adapter.calls) == 3

    def test_reinjection_format(self):
        adapter = MockAdapter(script=[
            "[INTERCEPT: 6*7]",
            "done",
        ])
        loop = InterceptionLoop(adapter, Intercepter(), max_iterations=3)
        loop.generate("original prompt")
        second_prompt = adapter.calls[1]["prompt"]
        assert "<observation>" in second_prompt
        assert "</observation>" in second_prompt
        assert "[RESULT: 42]" in second_prompt

    def test_cumulative_tokens(self):
        adapter = MockAdapter(script=["no tags"])
        loop = InterceptionLoop(adapter, Intercepter(), max_iterations=3)
        result = loop.generate("test")
        assert result.tokens_in > 0
        assert result.tokens_out > 0

    def test_error_in_loop(self):
        adapter = MockAdapter(script=["[INTERCEPT: 1/0]", "done"])
        loop = InterceptionLoop(adapter, Intercepter(), max_iterations=3)
        result = loop.generate("test")
        assert "ERROR" in result.text or "done" in result.text

    def test_temperature_passthrough(self):
        adapter = MockAdapter(script=["no tags"])
        loop = InterceptionLoop(adapter, Intercepter(), max_iterations=3)
        loop.generate("test", temperature=0.7)
        assert adapter.calls[0]["temperature"] == 0.7

    def test_max_tokens_passthrough(self):
        adapter = MockAdapter(script=["no tags"])
        loop = InterceptionLoop(adapter, Intercepter(), max_iterations=3)
        loop.generate("test", max_tokens=256)
        assert adapter.calls[0]["max_tokens"] == 256

    def test_schema_passthrough(self):
        adapter = MockAdapter(script=["no tags"])
        loop = InterceptionLoop(adapter, Intercepter(), max_iterations=3)
        schema = {"type": "object"}
        loop.generate("test", schema=schema)
        assert adapter.calls[0]["schema"] == schema

    def test_emit_fn_called(self):
        adapter = MockAdapter(script=["[INTERCEPT: 2+2]", "done"])
        events: list[dict] = []
        loop = InterceptionLoop(
            adapter, Intercepter(), max_iterations=3,
            emit_fn=lambda **kw: events.append(kw))
        loop.generate("test")
        assert len(events) >= 1
        assert events[0]["type"] == "tool_call"
        assert events[0]["category"] == "external"

    def test_emit_fn_none(self):
        adapter = MockAdapter(script=["no tags"])
        loop = InterceptionLoop(
            adapter, Intercepter(), max_iterations=3, emit_fn=None)
        result = loop.generate("test")
        assert result.text == "no tags"

