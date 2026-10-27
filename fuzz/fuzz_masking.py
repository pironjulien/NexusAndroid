"""Fuzzer Atheris des cibles de fuzz/targets.py (masquage et accords), lancé par .github/workflows/fuzz.yml."""

import sys

import atheris  # pyright: ignore[reportMissingImports]  (Linux seulement, fuzz/requirements.txt)

with atheris.instrument_imports():
    import targets


def main() -> None:
    atheris.Setup(sys.argv, targets.target)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
