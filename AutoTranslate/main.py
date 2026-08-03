#!/usr/bin/env python3
"""AutoTranslate launcher.

    python main.py            # graphical interface
    python main.py --cli ...  # headless batch mode (see --cli --help)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    if "--cli" in sys.argv:
        from autotranslate.cli import main as cli_main

        return cli_main(sys.argv[1:])

    try:
        import tkinter  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "Tkinter is missing.\n"
            "  Ubuntu/Debian : sudo apt install python3-tk\n"
            "  Fedora        : sudo dnf install python3-tkinter\n"
            "  Arch          : sudo pacman -S tk\n"
            "  Windows/macOS : reinstall Python with the Tcl/Tk option ticked\n"
            "Or run headless: python main.py --cli --help\n"
        )
        return 2

    from autotranslate.gui import main as gui_main

    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
