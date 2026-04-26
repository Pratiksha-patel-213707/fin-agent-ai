"""CLI entrypoint for journal validation."""

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate manual journal entries")
    parser.add_argument("--input-dir", default="input", help="Directory containing input files")
    parser.add_argument("--output-dir", default="output", help="Directory for output reports")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = parser.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY environment variable not set.")
        print("Set it before running the script.")
        sys.exit(1)

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    from agents.adjustments_agent import ManualAdjustmentsAgent
    from core.loaders import load_adjustments, load_coa
    from core.output import write_json_report, write_text_report

    print("\nLoading input files...")
    coa, ambiguous = load_coa(input_dir / "chart_of_accounts.csv")
    print(f"   Loaded COA: {len(coa)} accounts ({len(ambiguous)} marked ambiguous: {ambiguous})")

    adjustments = load_adjustments(input_dir / "manual_adjustments.json")
    print(f"   Loaded {len(adjustments)} manual adjustment entries")

    print("\nRunning validation...\n")
    agent = ManualAdjustmentsAgent(coa=coa, verbose=not args.quiet)
    result = agent.validate(adjustments)

    json_path = output_dir / f"validation_result_{result.period}.json"
    text_path = output_dir / f"validation_report_{result.period}.txt"
    write_json_report(result, json_path)
    write_text_report(result, text_path)

    print("\n" + "=" * 60)
    print("  VALIDATION COMPLETE")
    print("=" * 60)
    print(result.summary_plain_english)
    print()
    print(f"  Valid         : {result.valid_count}")
    print(f"  Needs review  : {result.needs_review_count}")
    print(f"  Blocked errors: {result.invalid_count}")
    print()
    print(f"  JSON report   : {json_path}")
    print(f"  Text report   : {text_path}")
    print("=" * 60)

    if result.invalid_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
