"""Importa todos os módulos do bot para achar import quebrado antes do deploy.

Uso: python scripts/check_imports.py
"""
from __future__ import annotations

import importlib
import os
import pkgutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402


def main() -> int:
    failures = 0
    for module in pkgutil.walk_packages(app.__path__, "app."):
        try:
            importlib.import_module(module.name)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERRO {module.name}: {exc!r}")
    print(f"{failures} erro(s) de import")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
