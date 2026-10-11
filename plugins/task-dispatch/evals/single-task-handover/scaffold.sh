#!/usr/bin/env bash
set -euo pipefail
git init -q -b main
git config user.name "Eval"
git config user.email "eval@example.invalid"
mkdir -p calc tests docs/plans
: > tests/__init__.py
printf 'def add(a, b):\n    return a + b\n' > calc/__init__.py
printf 'import unittest\n\nfrom calc import add\n\n\nclass CalcTests(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n' > tests/test_calc.py
printf '[project]\nname = "calc"\nversion = "0.1.0"\n' > pyproject.toml
printf '# calc\n\nQuality gate: `python3 -m unittest -q`. No Co-Authored-By trailers.\n' > CLAUDE.md
printf '# Plan\n\n- [ ] T1: Add `multiply(a, b)` with a test.\n- [ ] T2: Add `negate(a)` with a test.\n' > docs/plans/plan.md
git add -A
git commit -q -m "Initial calculator"
