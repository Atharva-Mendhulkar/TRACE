"""
TRACE CLI Entrypoint Bootstrap.
Resolves the 'trace' package namespace to avoid collision with Python's standard library 'trace.py'.
"""
import sys
from pathlib import Path


def bootstrap():
    # 1. Resolve candidate directories that contain the 'trace' package
    candidates = [
        Path(__file__).resolve().parent,
        Path.cwd().resolve(),
    ]
    for p in list(sys.path):
        try:
            candidates.append(Path(p).resolve())
        except Exception:
            pass

    for candidate in candidates:
        if (candidate / "trace" / "__init__.py").exists():
            cand_str = str(candidate)
            if cand_str in sys.path:
                sys.path.remove(cand_str)
            sys.path.insert(0, cand_str)
            break

    # 2. Evict standard library trace module if it was loaded prior to this
    if "trace" in sys.modules and not hasattr(sys.modules["trace"], "cli"):
        del sys.modules["trace"]


def main():
    bootstrap()
    from trace.cli.main import main as cli_main
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
