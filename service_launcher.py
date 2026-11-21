"""PyInstaller entrypoint for MouseShareService.exe (lock-screen helper, console)."""
from mouseshare.winlock import main

raise SystemExit(main())
