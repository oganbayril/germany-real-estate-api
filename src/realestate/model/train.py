"""`realestate-train` entrypoint: load data -> features -> CV -> fit -> eval -> persist. Phase 4."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="realestate-train", description="Train the price model.")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--from-sample",
        action="store_true",
        help="Train from sample/ CSV instead of the DB.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # TODO(phase-4): real training run; write models/<version>/ and print metrics
    print(
        f"[stub] would train test_size={args.test_size} "
        f"seed={args.seed} from_sample={args.from_sample}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
