"""
CLI Entry Point for ValueKit AI
Usage: python scripts/run.py --ticker AAPL --year 2024
"""

import argparse
import logging
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.valuekit_ai.config.config import PIPELINE_VERSION
from backend.valuekit_ai.core.valuekit_integration import ValueKitAnalyzer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s][%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="ValueKit AI CLI")
    parser.add_argument("--ticker", required=True, help="Stock ticker (e.g. AAPL)")
    parser.add_argument("--year", type=int, default=2024, help="Base year")
    parser.add_argument("--discount-rate", type=float, default=0.15)
    parser.add_argument("--mos", type=float, default=0.50, help="Margin of safety")
    parser.add_argument("--load-sec", action="store_true", help="Load SEC filings")
    parser.add_argument(
        "--load-earnings", action="store_true", help="Load earnings transcripts"
    )
    parser.add_argument("--no-moat", action="store_true", help="Skip moat analysis")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    import re

    ticker = args.ticker.strip().upper()
    if not re.match(r"^[A-Z]{1,5}$", ticker):
        log.error("[run][invalid_ticker] input='%s'", args.ticker)
        print(
            f"Error: Invalid ticker symbol '{args.ticker}'. Expected 1-5 uppercase letters."
        )
        sys.exit(1)

    log.info(
        "[run][start] ticker=%s year=%d discount=%.2f mos=%.2f pipeline_version=%s",
        ticker,
        args.year,
        args.discount_rate,
        args.mos,
        PIPELINE_VERSION,
    )

    from backend.valuekit_ai.config.analysis_config import AnalysisConfig

    config = AnalysisConfig(
        run_moat_analysis=not args.no_moat,
    )

    analyzer = ValueKitAnalyzer()

    try:
        result = analyzer.analyze_stock_complete(
            ticker=ticker,
            year=args.year,
            auto_estimate_growth=True,
            discount_rate=args.discount_rate,
            margin_of_safety=args.mos,
            load_sec_data=args.load_sec,
            load_earnings_data=args.load_earnings,
            config=config,
        )

        print("\n" + "=" * 70)
        print(f"VALUEKIT AI — {ticker} — {args.year}")
        print(f"Pipeline Version: {PIPELINE_VERSION}")
        print("=" * 70)
        print(f"Final Recommendation: {result['final_recommendation']}")

        if result.get("mos_result"):
            mos = result["mos_result"]
            print(f"\nMOS Analysis:")
            print(f"  Fair Value:    ${mos.get('Fair Value Today', 0):.2f}")
            print(f"  MOS Price:     ${mos.get('MOS Price', 0):.2f}")
            print(f"  Current Price: ${mos.get('Current Stock Price', 0):.2f}")
            print(f"  {mos.get('Price vs Fair Value', '')}")

        if result.get("profitability_result"):
            prof = result["profitability_result"]
            print(f"\nProfitability:")
            if prof.get("roe"):
                print(f"  ROE:  {prof['roe'] * 100:.1f}%")
            if prof.get("roic"):
                print(f"  ROIC: {prof['roic'] * 100:.1f}%")
            if prof.get("net_margin"):
                print(f"  Net Margin: {prof['net_margin'] * 100:.1f}%")

        if result.get("ai_decision"):
            ai = result["ai_decision"]
            print(f"\nAI Moat Analysis:")
            print(f"  Decision:    {ai.get('decision')}")
            print(f"  Confidence:  {ai.get('confidence')}")
            print(f"  Score:       {ai.get('overall_score')}/100")
            print(f"  Moat:        {ai.get('moat_strength')}")

        print("=" * 70 + "\n")

    except Exception as e:
        log.error("[run][error] ticker=%s error=%s", ticker, e)
        print(f"Analysis failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
