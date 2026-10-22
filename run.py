"""mini-ui-autotest 入口。真正的逻辑在 engine/cli，这里只是薄壳。

    python run.py --tag smoke
    python run.py --env test --workers 4
    python run.py --list
"""

import sys

from engine.cli import main

if __name__ == "__main__":
    sys.exit(main())
