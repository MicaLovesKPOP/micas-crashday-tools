"""Compatibility entry point; use package_tool.py for new workflows."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import package_tool

SPECS = {mid: (prefix, meta["version"], meta["license"],
               sum(row["archive"].endswith(".py") for row in layout["members"]))
         for mid, (meta, layout, prefix) in package_tool.load_specs().items()
         if mid in ("tile-splitter", "world-builder")}


def collect(module):
    if module not in SPECS:
        raise ValueError("Unknown paired add-on")
    return package_tool.collect(module)


def build(module, output_dir):
    if module not in SPECS:
        raise ValueError("Unknown paired add-on")
    return package_tool.build(module, output_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", choices=sorted(SPECS), required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    build(args.module, args.output_dir)
