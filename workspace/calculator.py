#!/usr/bin/env python3
"""
Copilot Workspace Calculator
Parses human-friendly math expressions with suffixes: k/m/b/t
Usage:
    python3 calculator.py "10 million + 5 million + 500k"
    python3 calculator.py        # runs built-in test suite
"""
import re
import sys

SUFFIXES = {
    'k': 1_000,
    'thousand': 1_000,
    'm': 1_000_000,
    'million': 1_000_000,
    'b': 1_000_000_000,
    'billion': 1_000_000_000,
    't': 1_000_000_000_000,
    'trillion': 1_000_000_000_000,
}


def parse_number(token: str) -> float:
    """Parse a single token like '10m', '5 million', '500k', '1.5b'."""
    token = token.strip().lower().replace(',', '')
    if not token:
        raise ValueError("empty token")
    # Try plain number first
    try:
        return float(token)
    except ValueError:
        pass
    # Try number + suffix (e.g. "10m", "1.5billion")
    for suffix, mult in sorted(SUFFIXES.items(), key=lambda x: -len(x[0])):
        if token.endswith(suffix):
            num_part = token[: -len(suffix)].strip()
            if num_part:
                try:
                    return float(num_part) * mult
                except ValueError:
                    pass
    raise ValueError(f"Cannot parse number: {token!r}")


def evaluate(expression: str) -> float:
    """
    Evaluate a simple +/- expression containing human-friendly numbers.
    Supports: "10 million + 5m + 500k - 1b"
    """
    # Normalise whitespace around operators so we can split reliably
    expr = re.sub(r'\s*([+\-])\s*', r' \1 ', expression.strip())
    tokens = expr.split()

    # Build list of (sign, value) pairs
    result = 0.0
    sign = 1
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == '+':
            sign = 1
            i += 1
            continue
        if tok == '-':
            sign = -1
            i += 1
            continue
        # Might be a number followed by a word-suffix on the next token
        # e.g. "5" then "million"
        if i + 1 < len(tokens) and tokens[i + 1].lower() in SUFFIXES:
            combined = tok + tokens[i + 1]
            value = parse_number(combined)
            i += 2
        else:
            value = parse_number(tok)
            i += 1
        result += sign * value

    return result


def format_result(value: float) -> str:
    """Pretty-print the result with commas."""
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.6g}"


def run_tests() -> None:
    """Built-in test suite."""
    cases = [
        ("10 million + 5 million + 500k", 15_500_000),
        ("10m + 5m + 500k", 15_500_000),
        ("1b - 500m", 500_000_000),
        ("1.5 million", 1_500_000),
        ("100k + 200k + 300k", 600_000),
        ("1t - 999b", 1_000_000_000),
        ("42", 42),
        ("0", 0),
    ]
    print()
    print("  Calculator Test Suite")
    print("  ─────────────────────────────────────────")
    passed = 0
    for expr, expected in cases:
        try:
            got = evaluate(expr)
            ok = abs(got - expected) < 1e-6
            status = "✅" if ok else "❌"
            if ok:
                passed += 1
        except Exception as e:
            status = "❌"
            got = f"ERROR: {e}"
            ok = False
        print(f"  {status}  {expr!r:40s} → {format_result(got) if ok else got}")
    print()
    print(f"  {passed}/{len(cases)} tests passed")
    print()
    if passed < len(cases):
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        run_tests()
    else:
        expression = " ".join(sys.argv[1:])
        try:
            result = evaluate(expression)
            print(format_result(result))
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
