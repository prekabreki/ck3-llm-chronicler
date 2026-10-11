"""``python -m chronicler`` entry point.

The ``chronicler`` console script points at ``chronicler.cli.main:main``.
``python -m chronicler`` needs this file so ``chronicler launch`` can spawn
the dev server with ``sys.executable -m chronicler`` without relying on the
console-script shim being on PATH (issue #9).
"""

from chronicler.cli.main import main

if __name__ == "__main__":
    main()
