from __future__ import annotations

import argparse

from data_pipeline import run_pipeline


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-schemes", type=int, default=100)
    args = parser.parse_args()
    run_pipeline(incremental=False, max_nav_schemes=args.max_schemes)
