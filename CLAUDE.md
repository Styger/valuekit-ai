# CLAUDE.md — ValueKit AI (WIPRO Edition)

This is a Streamlit-based stock analysis tool that combines quantitative value-investing
calculations (DCF, MOS, CAGR, ROIC) with a RAG system for qualitative moat analysis.
Built as a WIPRO thesis project at HSLU — academic research context, not production finance software.

---

## Repository Layout

```
valuekit-ai/
├── frontend/
│   └── app.py                          # Streamlit entry point — all UI lives here
├── backend/
│   ├── api/
│   │   └── fmp_api.py                  # Financial Modeling Prep API wrapper + SHA-256 cache
│   ├── logic/                          # Pure calculation modules (no UI, no RAG)
│   │   ├── __init__.py
│   │   ├── cagr.py                     # CAGR calculations, get_cagr_for_screening()
│   │   ├── mos.py                      # Margin of Safety (Buffett formula)
│   │   ├── pbt.py                      # Payback Time (8-year FCF)
│   │   ├── profitability.py            # ROE, ROA, ROIC, Net Margin, FCF Yield
│   │   ├── tencap.py                   # TenCap valuation
│   │   ├── fundamentals.py             # check_fundamentals() — D/E, FCF trend, margins
│   │   └── peer_comparison.py          # get_peers() — FMP first, Claude API fallback
│   ├── valuekit_ai/
│   │   ├── config/
│   │   │   ├── config.py               # PIPELINE_VERSION constant
│   │   │   └── analysis_config.py      # AnalysisConfig dataclass + get_enabled_moats()
│   │   ├── core/
│   │   │   ├── investment_analyzer.py  # InvestmentDecision, combined score logic
│   │   │   ├── moat_analyzer.py        # MoatAnalyzer — 5 moat types via RAG
│   │   │   └── valuekit_integration.py # ValueKitAnalyzer — orchestrates everything
│   │   ├── rag/
│   │   │   ├── rag_service.py          # analyze_with_rag() — Claude Sonnet, Temp=0.0
│   │   │   └── vector_store.py         # ChromaDB + Voyage AI (voyage-finance-2), TOP_K=5
│   │   └── data_pipeline/
│   │       └── load_sec_data.py        # load_company_data(ticker)
│   └── cache/                          # CacheManager — SHA-256 keyed, TTL-based
├── scripts/
│   └── run.py                          # CLI runner
├── valuekit_flowchart.mermaid          # Living architecture diagram — update when flow changes
└── .streamlit/
    └── secrets.toml                    # API keys (NOT committed)
```

---

## Key Architecture: 3-Score Model

The analysis pipeline produces three independent scores that combine into a final decision:

| Score               | Source                | Weight | Key Metrics                            |
| ------------------- | --------------------- | ------ | -------------------------------------- |
| **Quality Score**   | FMP quantitative data | 40%    | ROIC, FCF Yield, Net Margin, CAGR      |
| **Valuation Score** | MOS / PBT / TenCap    | 20%    | Price vs Fair Value per method         |
| **Moat Score**      | RAG (SEC + earnings)  | 40%    | Brand, Switching, Network, Cost, Scale |

```
Combined Score = (Quality × 0.40) + (Valuation × 0.20) + (Moat × 0.40) − Red Flag Penalty
Red Flag Penalty = min(25, red_flag_count × 5)
```

**Decision thresholds:**

- `≥80, 0 flags` → STRONG BUY
- `≥70, ≤1 flag` → BUY
- `≥50` → HOLD
- `<50` → PASS

**Critical rule:** Decision logic lives in exactly ONE place: `investment_analyzer.py → _make_decision()`.
`moat_analyzer.py → _generate_recommendation()` must call `_make_decision()`, never reimplement it.
`moat_strength` is computed from `moat_score` only, never from `combined_score`.

---

## Key Function Signatures

```python
# ── FMP API ──────────────────────────────────────────────────────────────────
fmp_api.get_year_data_by_range(ticker, start_year, years) → (data, mos_input)
fmp_api.get_current_price(ticker) → float
fmp_api.get_dcf(ticker) → dict
fmp_api.get_latest_common_year(year, balance, income, cashflow, ticker, show_warning) → {"year": int}

# ── Logic Layer ───────────────────────────────────────────────────────────────
cagr.run_analysis(ticker, start_year, end_year, period_years, include_*)
cagr._mos_growth_estimate_auto(data_dict, start_year, end_year, ...) → dict with "avg"
cagr.get_cagr_for_screening(ticker, period_years=5) → float  # decimal, e.g. 0.15

mos.calculate_mos_value_from_ticker(ticker, year, growth_rate, discount_rate, margin_of_safety) → dict

pbt.calculate_pbt_from_ticker(ticker, year, growth_estimate, return_full_table) → (buy_price, fair_value, table, price_info)
pbt.calculate_pbt_with_comparison(ticker, year, growth_rate, lang) → None  # prints

profitability.calculate_profitability_metrics_from_ticker(ticker, year) → dict
# Keys: roe, roa, roic, net_margin, gross_margin, (fcf_yield — add if missing)

fundamentals.check_fundamentals(ticker, year) → List[Dict]
# Each dict: {metric, value, status ("OK"/"Warning"/"Flag"/"N/A"), note, raw}
# 5 checks: Debt/Equity, FCF Trend (3Y), Gross Margin Trend, Net Margin Trend, Current Ratio

peer_comparison.get_peers(ticker) → List[str]  # max 5, FMP → Claude fallback, 7-day cache

# ── RAG Layer ─────────────────────────────────────────────────────────────────
rag_service.analyze_with_rag(query, quantitative_data=None, max_tokens=1024) → dict
# Returns: {status ("success"/"error"), analysis (str), sources (list), relevance_score (float)}

# ── Moat Analyzer ─────────────────────────────────────────────────────────────
MoatAnalyzer.analyze_business_model(ticker) → {status, description, sources_used}
MoatAnalyzer.analyze_single_moat(ticker, moat_key, moat_config) → MoatScore
MoatAnalyzer.analyze_moats(ticker, config) → MoatAnalysis
MoatAnalyzer.detect_red_flags(ticker, enabled_categories) → List[str]

# ── Integration Layer ─────────────────────────────────────────────────────────
ValueKitAnalyzer.analyze_stock_complete(
    ticker, year, auto_estimate_growth, discount_rate, margin_of_safety,
    load_sec_data, load_earnings_data, config
) → dict
# Result keys: mos_result, profitability_result, ai_decision, final_recommendation
# ai_decision keys: decision, confidence, overall_score, moat_strength, reasoning,
#                   red_flags, quantitative_score, qualitative_score
```

---

## AnalysisConfig

```python
# All flags default to True unless overridden
config = AnalysisConfig(
    run_cagr=True,
    run_mos=True,
    run_profitability=True,
    run_moat_analysis=True,
    run_brand_power=True,
    run_switching_costs=True,
    run_network_effects=True,
    run_cost_advantages=True,
    run_efficient_scale=True,
    run_red_flags=True,
)
config.get_enabled_moats()      # → list of enabled moat keys
config.get_enabled_red_flags()  # → list of enabled red flag categories
```

---

## Streamlit App Structure (`frontend/app.py`)

One function per page, no arguments, reads state internally:

```python
_page_overview()      # Runs full pipeline, shows 3-score summary
_page_cagr()          # Rolling CAGR table
_page_mos()           # MOS / Buffett formula
_page_profitability() # ROE, ROA, ROIC, Net Margin
_page_pbt()           # 8-year payback time
_page_moat()          # AI moat analysis + business model + fundamentals + peers
```

Page registration in `main()`:

```python
PAGE_FN = {
    "📊 Overview":                _page_overview,
    "📈 CAGR Growth Estimate":    _page_cagr,
    "🛡️ Margin of Safety (MOS)": _page_mos,
    "💰 Profitability Metrics":   _page_profitability,
    "⏱️ Payback Time (PBT)":     _page_pbt,
    "🤖 AI Moat Analysis":        _page_moat,
}
```

**Security requirements (WIPRO):**

- All tickers validated via `_validate_ticker()` before use — regex `^[A-Z]{1,5}$`
- Session limit enforced via `_check_session_limit()` at start of every analysis
- `MAX_ANALYSES_PER_SESSION = 10`
- Errors caught and displayed via `st.error()`, never let exceptions propagate to UI
- Logging pattern: `log.info("[app][event] ticker=%s year=%d ...", ticker, year)`

---

## Data Flow for New Features

When adding a new module in `backend/logic/`:

1. Create `backend/logic/your_module.py` with `root_dir` path setup:

```python
root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))
```

2. Add to `backend/logic/__init__.py`:

```python
from . import mos, cagr, profitability, tencap, pbt, fundamentals, your_module
```

3. Import in `frontend/app.py`:

```python
from backend.logic.your_module import your_function
```

---

## Caching Pattern

```python
from backend.cache import get_cache_manager

cache = get_cache_manager()
cached = cache.get(cache_key, "namespace")   # None if miss
if cached is not None:
    return cached
# ... compute result ...
cache.set(cache_key, "namespace", result)    # store
```

FMP API data is cached automatically via SHA-256 keyed files in `fmp_api.py`.
Peer comparison uses 7-day TTL under the `"fundamentals"` namespace.

---

## Claude API Calls (non-RAG)

Some modules call the Anthropic API directly (e.g. `peer_comparison.py` fallback).
Use this pattern:

```python
from anthropic import Anthropic

# Load key from .streamlit/secrets.toml or environment
api_key = _load_api_key()
client = Anthropic(api_key=api_key)

message = client.messages.create(
    model="claude-sonnet-4-20250514",  # always use this model
    max_tokens=80,                      # keep tight for structured output
    temperature=0.0,                    # always 0.0 for deterministic results
    messages=[{"role": "user", "content": prompt}],
)
raw = message.content[0].text.strip()
```

---

## Known Open Bugs (as of Week 3)

These are tracked and should not be "fixed" by workarounds — they need proper fixes:

1. **Quant Score = 0** — `_calculate_quantitative_score()` does not include ROIC/FCF/Net Margin.
   Fix: see `IMPLEMENTATION_PLAN.md` Milestone 2.

2. **PASS + Wide Moat contradiction** — `_generate_recommendation()` and `_combine_scores()`
   compute decisions independently. Fix: single `_make_decision()` in `investment_analyzer.py`.

3. **Fundamentals import error** — `fmp_api.get_balance_sheet()` / `get_income_statement()` method
   names may differ from what `fundamentals.py` expects. Check `fmp_api.py` for exact names.

4. **Peers empty for AAPL** — FMP `/v4/stock_peers` may require a higher plan tier.
   Claude fallback in `peer_comparison.py` should handle this but needs validation.

5. **MOS Fair Value too low** — auto-growth estimate via CAGR gives ~10% for AAPL (should be ~15–18%).
   Fix: Milestone 5 (3-source growth consensus with FMP analyst estimates).

6. **Moat Strength inconsistent** — Wide on Moat page, Narrow on Overview.
   Root cause: RAG is non-deterministic across calls. Fix: cache moat result per session.

---

## Flowchart

The file `valuekit_flowchart.mermaid` is the authoritative architecture diagram.
Update it whenever the pipeline changes. Export as PNG for thesis (via mermaid.live,
white background). Remove ⑤⑥⑦ labels before thesis export — these are dev markers only.

---

## Secrets Structure (`.streamlit/secrets.toml`)

```toml
[fmp]
api_key = "..."

[anthropic]
api_key = "..."

[voyage]
api_key = "..."

[auth.credentials.usernames.admin]
name = "Admin"
password = "$2b$12$..."  # bcrypt hash of HMAC-SHA256(pepper, password)

[auth]
cookie_name = "valuekit_auth"
cookie_key  = "..."          # random 32+ char secret (HMAC key for session cookie)
cookie_expiry_days = 1
pepper = "..."               # random 32+ char secret — never commit the real value

# To generate a new peppered hash, run:
#   python - <<'EOF'
#   import bcrypt, hashlib, hmac, getpass
#   pepper   = input("pepper: ")
#   pw       = getpass.getpass("password: ")
#   peppered = hmac.new(pepper.encode(), pw.encode(), hashlib.sha256).hexdigest()
#   print(bcrypt.hashpw(peppered.encode(), bcrypt.gensalt(12)).decode())
#   EOF
```

---

## Testing

No formal test suite. Use these quick smoke tests:

```bash
# Import check
python -c "from backend.logic.fundamentals import check_fundamentals; print('OK')"
python -c "from backend.logic.peer_comparison import get_peers; print(get_peers('AAPL'))"

# Run app
streamlit run frontend/app.py

# Test single module
python backend/logic/fundamentals.py     # has __main__ block
python backend/logic/peer_comparison.py  # has __main__ block
```

---

## Commit Convention

```
feat: short description of new feature
fix: short description of bug fixed
refactor: internal change, no behavior change
docs: flowchart or comment updates
```

Tag MVP completion: `git tag v1.0.0-mvp`

## Next Steps

Read `IMPLEMENTATION_PLAN.md` for the full task list. Start with Milestone 1.

## Python Environment

- Always use `streamlit run frontend/app.py` or `/c/Python312/python.exe` for testing
- Default `python` on this machine = Python 3.14 → chromadb incompatible
- Never use bare `python` for import checks
