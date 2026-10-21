"""Build the public legal/source artifacts using the shared, tested packager."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.source_package import main

if __name__ == "__main__":
    main()
