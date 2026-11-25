"""Compatibility entry point; use package_tool.py for new workflows."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import package_tool

MODULE_ID = 'crashday-exporter'

def collect():
    return package_tool.collect(MODULE_ID)

def build(output_dir):
    return package_tool.build(MODULE_ID, output_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    build(parser.parse_args().output_dir)
