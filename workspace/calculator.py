"""
calculator.py — Basic calculator with human-friendly number parsing.
Understands: k/K = 1,000 | m/M/million = 1,000,000 | b/B/billion = 1,000,000,000
"""
import re
import sys

SUFFIXES = {
    "k": 1_000,
    "m": 1_000_000,
    "million": 1_000_000,
    "b": 1_000_000_000,
    "billion": 1_000_000_000,
}

_NUM_RE = re.compile(
    r"(\d[\d,]*)(?:\.\d+)?\s*(billion|million|k|m|b)?",
    re.IGNORECASE,
)


def parse_number(token: str) -> float:
    token = token.strip().replace(",", "")
    m = _NUM_RE.fullmatch(token.strip())
    if not m:
        raise ValueError(f"Cannot parse number: {token!r}")
    digits = float(m.group(1).replace(",", ""))
    suffix = (m.group(2) or "").lower()
    return digits * SUFFIXES.get(suffix, 1)


def format_result(n: float) -> str:
    display = f"{n:,.0f}" if n == int(n) else f"{n:,.2f}"
    if abs(n) >= 1_000_000_000:
        label = f"{n / 1_000_000_000:.3g} billion"
    elif abs(n) >= 1_000_000:
        label = f"{n / 1_000_000:.3g} million"
    elif abs(n) >= 1_000:
        label = f"{n / 1_000:.3g} thousand"
    else:
        label = ""
    return f"{display}  ({label})" if label else display


class Calculator:
    def add(self, a, b):       return a + b
    def subtract(self, a, b):  return a - b
    def multiply(self, a, b):  return a * b
    def divide(self, a, b):
        if b == 0:
            raise ZeroDivisionError("Cannot divide by zero")
        return a / b

    def evaluate(self, expression: str) -> float:
        expr = expression.strip().lower()
        token_re = re.compile(
            r"([+\-*/])|(\d[\d,]*(?:\.\d+)?\s*(?:billion|million|k|m|b)?)",
            re.IGNORECASE,
        )
        tokens = token_re.findall(expr)
        numbers, operators = [], []
        for op, num in tokens:
            if op:
                operators.append(op)
            if num:
                numbers.append(parse_number(num.strip()))
        if not numbers:
            raise ValueError(f"No numbers found in: {expression!r}")
        result = numbers[0]
        for op, num in zip(operators, numbers[1:]):
            if op == "+":   result = self.add(result, num)
            elif op == "-": result = self.subtract(result, num)
            elif op == "*": result = self.multiply(result, num)
            elif op == "/": result = self.divide(result, num)
        return result


def run_tests():
    calc = Calculator()
    tests = [
        ("10 million + 5 million + 500k",  15_500_000),
        ("100k + 200k",                     300_000),
        ("1 billion - 500 million",         500_000_000),
        ("2 million * 3",                   6_000_000),
        ("10 million / 4",                  2_500_000),
        ("1k + 1k + 1k",                    3_000),
    ]
    print("=" * 58)
    print("  CALCULATOR TEST SUITE  (via Copilot-API Stack)")
    print("=" * 58)
    passed = 0
    for expr, expected in tests:
        result = calc.evaluate(expr)
        ok = result == expected
        if ok: passed += 1
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}]  {expr}")
        print(f"         = {format_result(result)}")
        if not ok:
            print(f"         Expected: {format_result(expected)}")
        print()
    print(f"  Results: {passed}/{len(tests)} passed")
    print("=" * 58)
    return passed == len(tests)


def main():
    calc = Calculator()
    if len(sys.argv) > 1:
        expression = " ".join(sys.argv[1:])
        result = calc.evaluate(expression)
        print(f"\n  {expression}")
        print(f"  = {format_result(result)}\n")
    else:
        run_tests()


if __name__ == "__main__":
    main()
