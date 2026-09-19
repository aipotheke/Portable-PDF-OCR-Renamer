"""PyInstaller entrypoint: imports the app package and runs its main()."""

from app.main import main

if __name__ == "__main__":
    raise SystemExit(main())
