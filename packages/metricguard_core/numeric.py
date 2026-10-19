"""Bounded scaled-integer numeric policy and exact arithmetic for MetricGuard.

Provides exact fixed-point parsing, scaled-integer arithmetic, and formatting
to eliminate floating-point and Decimal precision-truncation vulnerabilities
during evidence and comparison validation.
"""

import re
from decimal import Decimal
from typing import Iterable, Union


MAX_NUMERIC_STR_LEN = 128
MAX_NUMERIC_SCALE = 38
_PLAIN_DECIMAL = re.compile(r'(-?)([0-9]+)(?:\.([0-9]+))?')


def parse_fixed_point(
    val: str,
    max_len: int = MAX_NUMERIC_STR_LEN,
    max_scale: int = MAX_NUMERIC_SCALE,
) -> tuple[int, int]:
    """Parse a fixed-point numeric string into (coefficient: int, scale: int).

    Bounded Numeric Policy:
      - Type: str required.
      - String length: 1 <= len(val) <= max_len (default 128 chars).
      - Scale: 0 <= scale <= max_scale (default 38 digits).
      - Exponents: 'e' / 'E' forbidden unconditionally.
      - Non-finite tokens: 'NaN', 'Infinity', '-Infinity' forbidden unconditionally.
      - Whitespace: Leading, trailing, or internal whitespace forbidden.
      - Syntax: Optional leading sign ('+' or '-'), followed by non-empty digits,
        with at most one decimal point. Preserves legitimate historical forms
        (integer totals, formatted decimals, leading/trailing zeros).

    Accumulator Size Bounds & Scale Alignment:
      - Any single input representation is bounded by max_len = 128 chars and max_scale = 38.
      - The coefficient of an individual parsed number satisfies |C| < 10^128.
      - When aligning two numbers with scales s1, s2 in [0, 38], the scale shift is
        Delta s = max(s1, s2) - min(s1, s2) <= 38.
      - The aligned coefficient of an individual number satisfies:
          |C_aligned| < 10^128 * 10^38 = 10^166.
      - Summing up to N = 1,000,000 (MAX_EVIDENCE_ROWS) such terms yields an accumulator
        magnitude:
          |C_accum| < 10^6 * 10^166 = 10^172.
      - Storing 10^172 requires ceil(log2(10^172)) = ceil(571.37) = 572 bits (< 72 bytes).
      - Python arbitrary-precision integers represent numbers of this magnitude exactly
        without truncation, rounding, or precision limits.

    Returns:
      (coefficient, scale) where coefficient is a signed integer and scale >= 0.

    Raises:
      ValueError with structured messages ('invalid numeric representation',
      'nonfinite numeric value', or 'invalid numeric value').
    """
    # Fast path for the plain forms evidence uses ("-12.50", "3"): ASCII digits, an
    # optional leading minus and an optional fraction. It returns exactly what the
    # general path below returns for these inputs; everything else goes through it.
    if type(val) is str and len(val) <= max_len:
        match = _PLAIN_DECIMAL.fullmatch(val)
        if match is not None:
            negative, whole, fraction = match.groups()
            scale = len(fraction) if fraction is not None else 0
            if max_scale is None or scale <= max_scale:
                coeff = int(whole + fraction) if fraction is not None else int(whole)
                return (-coeff if negative else coeff), scale
    if not isinstance(val, str):
        raise ValueError("invalid numeric representation")
    if not val or len(val) > max_len:
        raise ValueError("invalid numeric representation")
    if "e" in val or "E" in val:
        raise ValueError("invalid numeric representation")
    if val != val.strip() or any(c.isspace() for c in val):
        raise ValueError("invalid numeric value")

    lower = val.lower()
    if "nan" in lower or "inf" in lower:
        raise ValueError("nonfinite numeric value")

    sign = -1 if val.startswith("-") else 1
    s = val[1:] if val.startswith(("+", "-")) else val
    if not s:
        raise ValueError("invalid numeric representation")

    parts = s.split(".")
    if len(parts) == 1:
        if not parts[0].isdigit():
            raise ValueError("invalid numeric value")
        coeff = sign * int(parts[0])
        scale = 0
    elif len(parts) == 2:
        int_part, frac_part = parts
        if not (int_part or frac_part):
            raise ValueError("invalid numeric representation")
        if int_part and not int_part.isdigit():
            raise ValueError("invalid numeric value")
        if frac_part and not frac_part.isdigit():
            raise ValueError("invalid numeric value")
        scale = len(frac_part)
        if max_scale is not None and scale > max_scale:
            raise ValueError("invalid numeric value")
        digits = (int_part or "0") + frac_part
        coeff = sign * int(digits)
    else:
        raise ValueError("invalid numeric value")

    return coeff, scale


class ScaledInteger:
    """Exact fixed-point decimal represented as a signed integer coefficient and scale.

    All operations (+, -, abs, ==, <, etc.) are evaluated using arbitrary-precision
    Python integers with scale alignment via powers of ten (10**(target_scale - scale)).
    Zero digits are ever lost, rounded, or clamped.
    """

    __slots__ = ("coeff", "scale")

    def __init__(self, coeff: int, scale: int):
        self.coeff = coeff
        self.scale = scale

    @classmethod
    def parse(
        cls,
        val: str,
        max_len: int = MAX_NUMERIC_STR_LEN,
        max_scale: int = MAX_NUMERIC_SCALE,
    ) -> "ScaledInteger":
        coeff, scale = parse_fixed_point(val, max_len=max_len, max_scale=max_scale)
        return cls(coeff, scale)

    @classmethod
    def zero(cls, scale: int = 0) -> "ScaledInteger":
        return cls(0, scale)

    def _align(self, other: "ScaledInteger") -> tuple[int, int, int]:
        target_scale = max(self.scale, other.scale)
        c1 = self.coeff * (10 ** (target_scale - self.scale))
        c2 = other.coeff * (10 ** (target_scale - other.scale))
        return c1, c2, target_scale

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ScaledInteger):
            c1, c2, _ = self._align(other)
            return c1 == c2
        if isinstance(other, int):
            c1, c2, _ = self._align(ScaledInteger(other, 0))
            return c1 == c2
        if isinstance(other, Decimal):
            try:
                dec_str = format(other, "f")
                other_scaled = ScaledInteger.parse(dec_str)
                c1, c2, _ = self._align(other_scaled)
                return c1 == c2
            except Exception:
                return False
        return False

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __sub__(self, other: "ScaledInteger") -> "ScaledInteger":
        c1, c2, s = self._align(other)
        return ScaledInteger(c1 - c2, s)

    def __add__(self, other: "ScaledInteger") -> "ScaledInteger":
        c1, c2, s = self._align(other)
        return ScaledInteger(c1 + c2, s)

    def __abs__(self) -> "ScaledInteger":
        return ScaledInteger(abs(self.coeff), self.scale)

    def to_decimal(self) -> Decimal:
        """Convert to Decimal preserving exact string representation without context truncation."""
        return Decimal(self.to_fixed_string())

    def to_fixed_string(self) -> str:
        """Serialize exactly as a fixed-point numeric string without exponents."""
        sign_str = "-" if self.coeff < 0 else ""
        abs_c = abs(self.coeff)
        if self.scale == 0:
            return f"{sign_str}{abs_c}"
        s_val = str(abs_c).zfill(self.scale + 1)
        int_p = s_val[:-self.scale]
        frac_p = s_val[-self.scale:]
        return f"{sign_str}{int_p}.{frac_p}"

    def __repr__(self) -> str:
        return f"ScaledInteger({self.to_fixed_string()!r}, coeff={self.coeff}, scale={self.scale})"
