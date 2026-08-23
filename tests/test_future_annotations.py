"""Tests for argklass's compatibility with ``from __future__ import annotations``
(PEP 563) and PEP 604 ``X | None`` unions.

Under PEP 563, every annotation in the *defining* module becomes a plain
string at runtime. Without resolving it back to a real type,
``is_dataclass(field.type)`` silently returns ``False`` for a nested
dataclass field, flattening what should be its own argparse section — and
``deduce_add_arguments`` has nowhere to recover the real type for building
the ``argparse`` action itself.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from argklass.arguments import argument_parser, is_optional


@dataclass
class ScheduleArgs:
    """Optimizer / LR schedule."""

    epochs: int = 10
    lr: float = 5e-4


@dataclass
class LossArgs:
    """Loss term weights."""

    sil_weight: float = 1.0
    pose_weight: float = 1.0


@dataclass
class TopArgs:
    """Top-level args with nested sections, defined under
    ``from __future__ import annotations`` — ScheduleArgs/LossArgs below are
    stringified annotations at runtime unless argklass resolves them."""

    device: str = "cuda"
    schedule: ScheduleArgs = field(default_factory=ScheduleArgs)
    loss: LossArgs = field(default_factory=LossArgs)


class TestNestedSectionsUnderFutureAnnotations:
    def test_sections_detected_and_reconstructed(self):
        parser = argument_parser(TopArgs)
        ns = parser.parse_args(
            ["--device", "cpu", "--epochs", "42", "--sil_weight", "0.0"]
        )
        top = ns.TopArgs

        assert isinstance(top.schedule, ScheduleArgs), (
            f"schedule is {type(top.schedule)}, not ScheduleArgs — "
            "nested-dataclass detection failed under future annotations"
        )
        assert isinstance(top.loss, LossArgs)
        assert top.device == "cpu"
        assert top.schedule.epochs == 42
        assert top.loss.sil_weight == 0.0
        assert top.loss.pose_weight == 1.0  # untouched default survives

    def test_defaults_only(self):
        parser = argument_parser(TopArgs)
        top = parser.parse_args([]).TopArgs
        assert top.schedule == ScheduleArgs()
        assert top.loss == LossArgs()


class TestUnresolvableAnnotationWarns:
    def test_warns_instead_of_silently_flattening(self):
        @dataclass
        class BrokenOuter:
            x: int = 1
            y: SomeUndefinedTypeXYZ = None  # noqa: F821 - intentionally unresolvable

        with pytest.raises(NameError):
            # The per-field argparse `type=` deduction for `y` still fails --
            # a genuinely undefined annotation can't become a CLI argument no
            # matter what. What we're checking is that the *class-level*
            # resolution failure is reported as a warning first, not silently
            # swallowed (which would previously fall back to raw/stringified
            # annotations for the *whole* dataclass with no diagnostic at all).
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                argument_parser(BrokenOuter)

        assert any(
            "could not resolve type hints" in str(w.message) for w in caught
        ), [str(w.message) for w in caught]


class TestPep604OptionalUnion:
    """`X | None` (PEP 604) must be recognized the same as `Optional[X]` —
    this matters once field.type is a *real* union object (e.g. after the
    future-annotations resolution above), not the raw string `cvt_type`
    special-cases."""

    def test_is_optional_pep604(self):
        assert is_optional(Path | None, None) is True
        assert is_optional(int | None, None) is True

    def test_is_optional_pep604_non_optional(self):
        assert is_optional(Path, None) is False
        assert is_optional(int | str, None) is False

    @dataclass
    class WithOptionalPath:
        out: Path | None = None

    def test_end_to_end_optional_path_field(self):
        parser = argument_parser(self.WithOptionalPath)
        ns = parser.parse_args(["--out", "some/dir"])
        assert ns.WithOptionalPath.out == Path("some/dir")

    def test_end_to_end_optional_path_field_default(self):
        parser = argument_parser(self.WithOptionalPath)
        ns = parser.parse_args([])
        assert ns.WithOptionalPath.out is None
