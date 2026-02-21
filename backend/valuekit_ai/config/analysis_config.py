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

    # Red Flags
    run_red_flags: bool = True

    # Individual Red Flag Types
    run_regulatory_risk: bool = True
    run_competitive_threats: bool = True
    run_management_issues: bool = True
    run_financial_stress: bool = True

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

    def get_enabled_red_flags(self) -> List[str]:
        """Get list of enabled red flag types"""
        flags = []
        if self.run_regulatory_risk:
            flags.append("regulatory_risk")
        if self.run_competitive_threats:
            flags.append("competitive_threats")
        if self.run_management_issues:
            flags.append("management_issues")
        if self.run_financial_stress:
            flags.append("financial_stress")
        return flags


# Preset Configurations
def quick_config() -> AnalysisConfig:
    """Quick analysis - all features enabled except profitability"""
    return AnalysisConfig(
        run_mos=True,
        run_cagr=True,
        run_profitability=False,
        run_moat_analysis=True,
        run_red_flags=True,
        auto_estimate_growth=True,
        load_sec_data=False,
        load_earnings_data=False,
    )


def quantitative_only() -> AnalysisConfig:
    """Quantitative analysis only - no AI moats"""
    return AnalysisConfig(
        run_mos=True,
        run_cagr=True,
        run_profitability=True,
        run_moat_analysis=False,
        run_red_flags=False,
        auto_estimate_growth=True,
        load_sec_data=False,
    )


def qualitative_only() -> AnalysisConfig:
    """Qualitative analysis only - AI moats without numbers"""
    return AnalysisConfig(
        run_mos=False,
        run_cagr=False,
        run_profitability=False,
        run_moat_analysis=True,
        run_red_flags=True,
        auto_estimate_growth=False,
        load_sec_data=True,  # Need SEC data for moats
    )


def deep_analysis() -> AnalysisConfig:
    """Deep analysis - everything enabled"""
    return AnalysisConfig(
        run_mos=True,
        run_cagr=True,
        run_profitability=True,
        run_moat_analysis=True,
        run_red_flags=True,
        auto_estimate_growth=True,
        load_sec_data=True,
        load_earnings_data=True,
        earnings_quarters=4,
    )
