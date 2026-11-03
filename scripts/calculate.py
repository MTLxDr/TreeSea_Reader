"""Stable command-line entry point; use --schema to export the input contract."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader.calculation_api import main

if __name__=='__main__':raise SystemExit(main())
