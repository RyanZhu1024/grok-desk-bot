"""Entry point for the screenless Haro build.

Equivalent to ``desk-assistant --variant haro``.
"""

from __future__ import annotations

import sys

from desk_assistant.__main__ import main as desk_main


def main(argv: list[str] | None = None) -> int:
    extra = list(sys.argv[1:] if argv is None else argv)
    return desk_main(["--variant", "haro", *extra])


if __name__ == "__main__":
    sys.exit(main())
