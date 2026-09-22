"""Per-user installer entry point shared by both native setup bundles."""
from chartcleaner.updater import installer_main

if __name__ == "__main__":
    raise SystemExit(installer_main())
