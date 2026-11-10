"""Entry point for Streamlit Community Cloud.

Cloud runs a script from the repository root with the root on sys.path, and it
does not pip-install the project. The package lives under src/, so
`from death_zone...` fails there without this shim. Locally either works:

    streamlit run streamlit_app.py                      # same as Cloud
    streamlit run src/death_zone/dashboard/app.py        # needs the install

Deployment also needs data/gold/*.parquet present in the checkout -- the app
reads those files and cannot build them, since the 43 MB upstream archive is
not redistributed here.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

runpy.run_module("death_zone.dashboard.app", run_name="__main__")
