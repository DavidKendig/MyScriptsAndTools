"""Allow `python -m autotranslate`."""

import sys

if "--cli" in sys.argv:
    from .cli import main

    sys.exit(main(sys.argv[1:]))
else:
    from .gui import main

    main()
