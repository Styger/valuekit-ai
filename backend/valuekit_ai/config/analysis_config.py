"""
Analysis Configuration System
Toggle which components to include in analysis
"""

from dataclasses import dataclass
import sys
from pathlib import Path
from typing import List

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))


@dataclass
class AnalysisConfig:
    """Configuration for ValueKit AI analysis"""

    # Quantitative Analysis Flags
    run_mos: bool = True  # Margin of Safety calculation
    run_cagr: bool = True  # Growth rate estimation
    run_profitability: bool = False  # Profitability metrics (ROE, ROA, ROIC, Margins)

    # Qualitative Analysis Flags
    run_moat_analysis: bool = True  # Master toggle for all moat analysis

    # Individual Moat Flags (only used if run_moat_analysis=True)
    run_brand_power: bool = True
    run_switching_costs: bool = True
    run_network_effects: bool = True
    run_cost_advantages: bool = True
    run_efficient_scale: bool = True

    # Parameters
    margin_of_safety: float = 0.50  # 50% safety margin
    discount_rate: float = 0.15  # 15% discount rate
    auto_estimate_growth: bool = True  # Auto-estimate from CAGR
    load_sec_data: bool = False  # Load SEC filings for AI analysis
    load_earnings_data: bool = False  # 🆕 Load earnings call transcripts (FMP API)
    earnings_quarters: int = 4  # 🆕 Number of quarters to fetch (default 1 year)

    def get_enabled_moats(self) -> List[str]:
        """Get list of enabled moat types"""
        moats = []
        if self.run_brand_power:
            moats.append("brand_power")
        if self.run_switching_costs:
            moats.append("switching_costs")
        if self.run_network_effects:
            moats.append("network_effects")
        if self.run_cost_advantages:
            moats.append("cost_advantages")
        if self.run_efficient_scale:
            moats.append("efficient_scale")
        return moats



def quantitative_only() -> AnalysisConfig:
    """Quantitative analysis only - no AI moats"""
    return AnalysisConfig(
        run_mos=True,
        run_cagr=True,
        run_profitability=True,
        run_moat_analysis=False,
        auto_estimate_growth=True,
        load_sec_data=False,
    )


