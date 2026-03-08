# ValueKit AI — Implementierungsplan: Neuer Flow

**Stand: Woche 3 | Ziel: Flowchart v3 vollständig implementiert**

---

## Übersicht

Der Plan hat **9 Milestones** und baut aufeinander auf. Jeder Milestone ist unabhängig abschliessbar und commit-würdig.

```
MILESTONE 1  → Files einspielen            [~15 min]
MILESTONE 2  → Bug: Quant Score = 0        [~1–2h]
MILESTONE 3  → Bug: PASS + Wide Moat       [~30 min]
MILESTONE 4  → Bug: Fundamentals & Peers   [~1h]
MILESTONE 5  → ⑥ Wachstumsrate Konsens    [~2–3h]
MILESTONE 6  → ⑦ Data Quality Check       [~1–2h]
MILESTONE 7  → ⑤ Peer Vergleichstabelle   [~2–3h]
MILESTONE 8  → Overview Update             [~1h]
MILESTONE 9  → Final Test & Cleanup        [~30 min]
```

**Gesamtschätzung: ~2 Arbeitstage**

---

## MILESTONE 1 — Vorhandene Output-Files einspielen

**Ziel:** Alle aus der letzten Session erarbeiteten Files sind im Repo an der richtigen Stelle.

### Schritt 1.1 — Files kopieren

Kopiere diese Files aus `/mnt/user-data/outputs/` in das Repo:

| Output-File          | Ziel im Repo                                |
| -------------------- | ------------------------------------------- |
| `moat_analyzer.py`   | `backend/valuekit_ai/core/moat_analyzer.py` |
| `fundamentals.py`    | `backend/logic/fundamentals.py`             |
| `peer_comparison.py` | `backend/logic/peer_comparison.py`          |
| `logic__init__.py`   | `backend/logic/__init__.py`                 |

### Schritt 1.2 — app.py: Imports hinzufügen

Füge am Ende des Import-Blocks in `frontend/app.py` ein:

```python
from backend.logic.fundamentals import check_fundamentals
from backend.logic.peer_comparison import get_peers
```

### Schritt 1.3 — app.py: `_page_moat()` ersetzen

Die aktuelle `_page_moat()` in `app.py` durch die Version aus `app_moat_patch.py` ersetzen.
(Komplette Funktion kopieren — enthält Business Model Expander + Fundamentals-Section)

### Schritt 1.4 — Smoke Test

```bash
streamlit run frontend/app.py
```

- Navigiere zu „🤖 AI Moat Analysis"
- Eingabe: `AAPL`, Jahr: `2024`
- Kein Importfehler = Milestone abgeschlossen ✅

### Git Commit

```bash
git add .
git commit -m "feat: apply fundamentals, peer_comparison, moat_analyzer, app patch"
```

---

## MILESTONE 2 — Bug Fix: Quant Score immer 0

**Problem:** `quantitative_score` in `InvestmentDecision` ist immer 0, weil der Quality Score (ROIC, FCF Yield, Net Margin, CAGR) nicht berechnet wird.
**Ziel:** Quant Score basiert auf echten Kennzahlen, nicht nur MOS-Preis.

### Schritt 2.1 — Diagnose

Öffne `backend/valuekit_ai/core/investment_analyzer.py`.
Suche nach `quantitative_score` und `_calculate_quant_score()` (oder ähnlich).

Überprüfe ob diese Scoring-Logik existiert (aus dem Architektur-Dokument):

```python
# Quant Score (0-100) laut Spec:
# ROIC   ≥30% → 40pts  | ≥20% → 30 | ≥15% → 20 | ≥10% → 10
# FCF Y  ≥8%  → 20pts  | ≥5%  → 15 | ≥3%  → 10 | ≥1%  → 5
# ...aber fehlt CAGR + Net Margin in aktueller Impl.
```

### Schritt 2.2 — Quality Score implementieren

In `investment_analyzer.py` die `_calculate_quantitative_score()`-Methode so anpassen:

```python
def _calculate_quantitative_score(self, profitability_result: dict, mos_result: dict) -> int:
    """
    Quality Score: 0–100
    Basiert auf: ROIC, FCF Yield, Net Margin, CAGR (historisch)
    Gewichtung: 40 / 20 / 20 / 20
    """
    score = 0

    # ── ROIC (max 40 Punkte) ──────────────────────────────────────────
    roic = profitability_result.get("roic") or 0
    if roic >= 0.30:   score += 40
    elif roic >= 0.20: score += 30
    elif roic >= 0.15: score += 20
    elif roic >= 0.10: score += 10

    # ── FCF Yield (max 20 Punkte) ─────────────────────────────────────
    # FCF Yield = FCF per Share / Current Price
    # Holen aus mos_result oder direkt via FMP
    fcf_yield = profitability_result.get("fcf_yield") or 0
    if fcf_yield >= 0.08:   score += 20
    elif fcf_yield >= 0.05: score += 15
    elif fcf_yield >= 0.03: score += 10
    elif fcf_yield >= 0.01: score += 5

    # ── Net Margin (max 20 Punkte) ────────────────────────────────────
    nm = profitability_result.get("net_margin") or 0
    if nm >= 0.20:   score += 20
    elif nm >= 0.15: score += 15
    elif nm >= 0.10: score += 10
    elif nm >= 0.05: score += 5

    # ── CAGR (max 20 Punkte) — aus growth_rate_used ──────────────────
    cagr = mos_result.get("Growth Rate", 0) / 100 if mos_result else 0
    if cagr >= 0.20:   score += 20
    elif cagr >= 0.15: score += 15
    elif cagr >= 0.10: score += 10
    elif cagr >= 0.05: score += 5

    return min(100, score)
```

> **Wichtig:** Falls `fcf_yield` nicht in `profitability_result` ist, muss es in
> `backend/logic/profitability.py` hinzugefügt werden:
> `fcf_yield = fcf_per_share / current_price if current_price > 0 else None`

### Schritt 2.3 — Combined Score Gewichtung prüfen

Stelle sicher, dass `_calculate_combined_score()` in `investment_analyzer.py` so aussieht:

```python
combined = (
    quality_score  * 0.40 +
    valuation_score * 0.20 +
    moat_score_normalized * 0.40
) - red_flag_penalty
```

(Nicht: `quantitative_score * 0.60 + moat * 0.40` — das war das alte Modell)

### Schritt 2.4 — Test

```
AAPL, 2024, load_sec=False → Combined Score sollte jetzt > 0 sein
Expected: Quality ~65–75 (ROIC ~25%, Net Margin ~26%)
```

### Git Commit

```bash
git commit -m "fix: quality score now uses ROIC + FCF Yield + Net Margin + CAGR"
```

---

## MILESTONE 3 — Bug Fix: PASS + Wide Moat Widerspruch

**Problem:** `_generate_recommendation()` und `_combine_scores()` berechnen die Entscheidung unabhängig und kommen zu widersprüchlichen Ergebnissen.
**Ziel:** Eine einzige Logik entscheidet, beide geben dasselbe aus.

### Schritt 3.1 — Diagnose

Suche in `investment_analyzer.py` nach:

- `_generate_recommendation()` — was gibt sie zurück?
- `_combine_scores()` — was gibt sie zurück?
- Wo wird `moat_strength` gesetzt? Ist es dieselbe Variable?

Typisches Problem: `moat_strength` wird aus dem Raw Moat Score berechnet (z.B. Wide bei Score 70+),
aber `decision` (PASS/BUY etc.) wird aus `combined_score` berechnet ohne `moat_strength` zu nutzen.

### Schritt 3.2 — Fix

**Regel: Die Entscheidungs-Logik muss genau einmal existieren, am Ende der Pipeline.**

```python
def _make_decision(self, combined_score: int, red_flag_count: int) -> tuple[str, str]:
    """
    Einzige Entscheidungslogik — laut Flowchart v3.
    Returns: (decision, confidence)
    """
    if combined_score >= 80 and red_flag_count == 0:
        return "STRONG BUY", "High"
    elif combined_score >= 70 and red_flag_count <= 1:
        return "BUY", "Medium"
    elif combined_score >= 50:
        return "HOLD", "Medium"
    else:
        return "PASS", "Low"
```

`_generate_recommendation()` soll diese Funktion aufrufen — nicht selber rechnen.

### Schritt 3.3 — Moat Strength konsistent machen

`moat_strength` soll **nur** aus `moat_score` berechnet werden (nicht aus `combined_score`):

```python
def _moat_strength_from_score(self, moat_score_normalized: int) -> str:
    if moat_score_normalized >= 65: return "Wide"
    elif moat_score_normalized >= 40: return "Narrow"
    else: return "None"
```

Diese Funktion sowohl in `moat_analyzer.py` als auch in `investment_analyzer.py` nutzen.

### Schritt 3.4 — Test

```
Szenario 1: AAPL (hoher Moat, gutes Quant) → Wide Moat + BUY/STRONG BUY ✅
Szenario 2: Günstiges Unternehmen, Moat schwach → Narrow/None + evtl. HOLD ✅
Kein Fall darf Wide Moat + PASS ausgeben.
```

### Git Commit

```bash
git commit -m "fix: unified decision logic, no more PASS+Wide contradiction"
```

---

## MILESTONE 4 — Bug Fix: Fundamentals & Peers

**Ziel:** Beide Features laden ohne Fehler.

### Schritt 4.1 — Fundamentals: Import debuggen

Füge temporär in `app.py` oben ein:

```python
try:
    from backend.logic.fundamentals import check_fundamentals
    print("✅ fundamentals import OK")
except Exception as e:
    print(f"❌ fundamentals import FAILED: {e}")
```

Lauf dann: `python frontend/app.py` im Terminal (nicht streamlit).

Häufige Ursachen:

- Falscher `root_dir` in `fundamentals.py` (prüfe die `Path(__file__).resolve().parent.parent.parent` Kette)
- `fmp_api.get_balance_sheet()` existiert nicht → in `fmp_api.py` nachschauen wie das korrekte Methode heisst
- `fmp_api.get_latest_common_year()` existiert nicht → diesen Call durch manuelle Year-Lookup ersetzen

### Schritt 4.2 — Fundamentals: FMP Methoden-Namen prüfen

Öffne `backend/api/fmp_api.py` und suche nach:

- Balance Sheet: `get_balance_sheet` oder anders?
- Income Statement: `get_income_statement` oder `get_financials`?
- Cashflow: `get_cashflow_statement` oder `get_cash_flow`?

Passe `fundamentals.py` entsprechend an.

### Schritt 4.3 — Peers: FMP Endpoint testen

Teste direkt im Terminal:

```python
from backend.logic.peer_comparison import _fetch_peers_fmp
print(_fetch_peers_fmp("AAPL"))
```

Falls leer: FMP `/v4/stock_peers` braucht möglicherweise einen kostenpflichtigen Plan.
→ Fallback auf Claude-API direkt testen:

```python
from backend.logic.peer_comparison import _fetch_peers_claude
print(_fetch_peers_claude("AAPL"))
```

### Schritt 4.4 — Peers: Claude Fallback sicherstellen

In `peer_comparison.py` sicherstellen, dass:

1. `ANTHROPIC_API_KEY` via `os.environ.get("ANTHROPIC_API_KEY")` geholt wird
2. Claude gibt nur Ticker zurück (Regex-Validation bereits implementiert)
3. Fallback wird wirklich aufgerufen wenn FMP leer → Log-Meldung prüfen

### Schritt 4.5 — Test

```
AAPL, 2024:
- Fundamentals: 5 Rows mit ✅/⚠️/🚩 sichtbar
- Peers: ["MSFT", "GOOGL", ...] sichtbar (min. 1 Peer)
```

### Git Commit

```bash
git commit -m "fix: fundamentals import path, peers FMP fallback to Claude"
```

---

## MILESTONE 5 — ⑥ Wachstumsrate: 3-Quellen-Konsens

**Ziel:** Growth Rate kommt nicht mehr aus einer fragilen Auto-Schätzung, sondern aus 3 Quellen mit Transparent-Anzeige woher die Zahl stammt.

### Schritt 5.1 — Neue Datei: `backend/logic/growth_consensus.py`

```python
"""
3-Quellen-Konsens für Wachstumsrate.
Quellen: (1) eigener CAGR  (2) FMP Analyst Estimates  (3) 25% Cap
"""

def get_growth_consensus(ticker: str, year: int) -> dict:
    """
    Returns:
        {
          "rate": float,          # finale Wachstumsrate (als Dezimal, z.B. 0.12)
          "sources": {
              "own_cagr": float | None,
              "analyst_estimate": float | None,
          },
          "method": str,          # "consensus" | "own_cagr_only" | "analyst_only" | "fallback"
          "capped": bool          # True wenn 25% Cap angewendet wurde
        }
    """
    own_cagr = _get_own_cagr(ticker, year)            # aus cagr.get_cagr_for_screening()
    analyst  = _get_fmp_analyst_estimate(ticker, year) # aus FMP /v3/analyst-estimates

    sources = {"own_cagr": own_cagr, "analyst_estimate": analyst}

    # Gewichtung: CAGR 60%, Analyst 40% — nur wenn beide vorhanden
    if own_cagr is not None and analyst is not None:
        raw = own_cagr * 0.60 + analyst * 0.40
        method = "consensus"
    elif own_cagr is not None:
        raw = own_cagr
        method = "own_cagr_only"
    elif analyst is not None:
        raw = analyst
        method = "analyst_only"
    else:
        raw = 0.10  # Fallback: 10%
        method = "fallback"

    # 25% Cap
    capped = raw > 0.25
    rate = min(raw, 0.25)

    return {"rate": rate, "sources": sources, "method": method, "capped": capped}
```

### Schritt 5.2 — FMP Analyst Estimates implementieren

```python
def _get_fmp_analyst_estimate(ticker: str, year: int) -> float | None:
    """
    Holt Analyst Growth Estimate von FMP.
    Endpoint: /v3/analyst-estimates/{ticker}?limit=5
    Feld: revenueAvg oder epsAvg growth YoY
    """
    try:
        data = fmp_api.get_analyst_estimates(ticker, limit=5)
        # Berechne YoY EPS Growth aus den Estimates
        # FMP gibt: {"estimatedEpsAvg": 6.5, "date": "2025-12-31", ...}
        # Finde die Estimate für year+1 vs year
        ...
    except:
        return None
```

> Prüfe zuerst ob `fmp_api.get_analyst_estimates()` existiert. Falls nicht, direkt
> per `requests.get()` implementieren analog zu `peer_comparison.py`.

### Schritt 5.3 — Integration in MOS Page

In `_page_mos()`: Statt dem bisherigen `_mos_growth_estimate_auto()` Aufruf:

```python
from backend.logic.growth_consensus import get_growth_consensus

if auto_cagr:
    consensus = get_growth_consensus(ticker, year)
    growth_rate = consensus["rate"]

    # Transparenz-Box
    method_labels = {
        "consensus":    "Eigener CAGR (60%) + Analyst-Schätzung (40%)",
        "own_cagr_only": "Eigener CAGR (keine Analyst-Daten verfügbar)",
        "analyst_only":  "Analyst-Schätzung (kein CAGR berechenbar)",
        "fallback":      "Standard-Fallback 10%",
    }
    label = method_labels.get(consensus["method"], "Unbekannt")
    cap_note = " ⚠️ auf 25% gekappt" if consensus["capped"] else ""

    st.info(
        f"**Wachstumsrate:** {growth_rate * 100:.1f}%{cap_note}  \n"
        f"📐 Methode: {label}  \n"
        f"Eigener CAGR: {consensus['sources']['own_cagr'] * 100:.1f}% | "
        f"Analyst: {(consensus['sources']['analyst_estimate'] or 0) * 100:.1f}%"
    )
```

### Schritt 5.4 — Integration in Overview

Dasselbe in `_page_overview()` — ersetze die Auto-CAGR-Berechnung.

### Schritt 5.5 — Test

```
AAPL, 2024, auto_cagr=True:
- Box zeigt Methode und beide Quellen
- Growth Rate sollte realistischer sein als bisher (~15–18% statt 10%)
```

### Git Commit

```bash
git commit -m "feat: 3-source growth consensus (own CAGR + FMP analyst + 25% cap)"
```

---

## MILESTONE 6 — ⑦ Data Quality Check (RAG Indicator)

**Ziel:** User sieht wenn eine Moat-Analyse auf wenigen oder schlecht relevanten Quellen basiert.

### Schritt 6.1 — Relevance Score aus RAG extrahieren

In `backend/valuekit_ai/rag/rag_service.py` nach `relevance_score` suchen.
Dieser Score existiert laut Architektur-Docs bereits.

Stelle sicher, dass `analyze_moats()` in `moat_analyzer.py` den Durchschnitt des Relevance Scores
und die Anzahl Sources zurückgibt:

```python
# In MoatAnalysis Dataclass ergänzen:
@dataclass
class MoatAnalysis:
    ...
    avg_relevance_score: float = 0.0   # NEU
    total_sources_used: int = 0        # NEU
```

In `analyze_moats()` nach allen RAG-Calls sammeln:

```python
relevance_scores = []
for moat_key in enabled_moats:
    rag_result = rag_service.analyze_with_rag(query, quant_data)
    relevance_scores.append(rag_result.get("relevance_score", 0))
    ...

analysis.avg_relevance_score = sum(relevance_scores) / len(relevance_scores) if relevance_scores else 0
analysis.total_sources_used = sum(r.get("sources_used", 0) for r in rag_results)
```

### Schritt 6.2 — Quality Tier definieren

```python
def _get_data_quality_tier(avg_relevance: float, sources: int) -> dict:
    if avg_relevance >= 0.65 and sources >= 5:
        return {"level": "high",    "label": "✅ Hohe Datenqualität",    "color": "success"}
    elif avg_relevance >= 0.45 or sources >= 3:
        return {"level": "medium",  "label": "⚠️ Mittlere Datenqualität", "color": "warning"}
    else:
        return {"level": "low",     "label": "🚩 Wenig Quelldaten",       "color": "error"}
```

### Schritt 6.3 — UI Integration in `_page_moat()`

Nach dem Spinner, vor den Ergebnissen:

```python
ai = result.get("ai_decision", {})
quality_tier = _get_data_quality_tier(
    avg_relevance=ai.get("avg_relevance_score", 0),
    sources=ai.get("total_sources_used", 0)
)

if quality_tier["level"] == "low":
    st.error(
        f"{quality_tier['label']} — Analyse basiert auf wenigen Dokumenten. "
        f"Ergebnis mit Vorsicht interpretieren."
    )
elif quality_tier["level"] == "medium":
    st.warning(
        f"{quality_tier['label']} — {ai.get('total_sources_used', 0)} Dokumentenabschnitte "
        f"analysiert. Ergebnis könnte unvollständig sein."
    )
else:
    st.success(
        f"{quality_tier['label']} — {ai.get('total_sources_used', 0)} relevante "
        f"Abschnitte analysiert."
    )
```

### Schritt 6.4 — ai_decision dict ergänzen

In `investment_analyzer.py` sicherstellen dass `to_dict()` die neuen Felder enthält:

```python
"avg_relevance_score": moat_analysis.avg_relevance_score,
"total_sources_used":  moat_analysis.total_sources_used,
```

### Schritt 6.5 — Test

```
AAPL, load_sec=True:  → Hohe oder Mittlere Qualität sichtbar
AAPL, load_sec=False: → Banner "Wenig Quelldaten" sichtbar
```

### Git Commit

```bash
git commit -m "feat: RAG data quality indicator (relevance score + source count)"
```

---

## MILESTONE 7 — ⑤ Peer Vergleichstabelle

**Ziel:** Statt nur Ticker-Liste zeigt das Tool eine Tabelle: Ticker | ROIC | Net Margin | CAGR | MOS%.

### Schritt 7.1 — Neue Funktion: `get_peer_metrics()`

In `backend/logic/peer_comparison.py` ergänzen:

```python
def get_peer_metrics(ticker: str, year: int) -> pd.DataFrame:
    """
    Für jeden Peer + das Subject: ROIC, Net Margin, CAGR, MOS% via FMP.
    Kein RAG, nur quantitative Daten.

    Returns: DataFrame mit Spalten [Ticker, ROIC, Net Margin, CAGR, MOS%]
    """
    peers = get_peers(ticker)
    all_tickers = [ticker] + peers

    rows = []
    for t in all_tickers:
        try:
            prof = profitability.calculate_profitability_metrics_from_ticker(t, year)
            cagr = cagr_module.get_cagr_for_screening(t, period_years=5)
            mos  = _get_mos_pct(t, year)  # eigene Hilfsfunktion
            rows.append({
                "Ticker":     t,
                "ROIC":       f"{prof.get('roic', 0) * 100:.1f}%" if prof.get('roic') else "N/A",
                "Net Margin": f"{prof.get('net_margin', 0) * 100:.1f}%" if prof.get('net_margin') else "N/A",
                "CAGR (5J)":  f"{cagr * 100:.1f}%" if cagr else "N/A",
                "MOS%":       f"{mos:.1f}%" if mos else "N/A",
                "_is_subject": t.upper() == ticker.upper(),
            })
        except Exception as e:
            log.warning("[peer_metrics] ticker=%s error=%s", t, e)
            rows.append({"Ticker": t, "ROIC": "N/A", "Net Margin": "N/A",
                        "CAGR (5J)": "N/A", "MOS%": "N/A", "_is_subject": False})

    return pd.DataFrame(rows)
```

### Schritt 7.2 — MOS% Hilfsfunktion

```python
def _get_mos_pct(ticker: str, year: int) -> float | None:
    """Berechnet MOS% (Current Price vs. Fair Value) für Peer-Vergleich."""
    try:
        result = calculate_mos_value_from_ticker(
            ticker=ticker, year=year,
            growth_rate=cagr_module.get_cagr_for_screening(ticker),
            discount_rate=0.15, margin_of_safety=0.50
        )
        fair_value = result.get("Fair Value Today")
        current    = result.get("Current Stock Price")
        if fair_value and current and current > 0:
            return (fair_value - current) / current * 100
    except:
        pass
    return None
```

### Schritt 7.3 — UI Integration in `_page_moat()`

Nach den Moat Results, vor dem Ende:

```python
# ── Peer Vergleich ───────────────────────────────────────────────────────
st.markdown("---")
st.subheader("🏢 Peer Vergleich")

with st.spinner("Lade Peer-Daten..."):
    try:
        peer_df = get_peer_metrics(ticker, year)
        display_df = peer_df.drop(columns=["_is_subject"])

        # Subject-Zeile hervorheben
        def highlight_subject(row):
            is_subj = peer_df.loc[row.name, "_is_subject"]
            return ["font-weight: bold; background-color: #e8f5e9"] * len(row) if is_subj else [""] * len(row)

        st.dataframe(
            display_df.style.apply(highlight_subject, axis=1),
            use_container_width=True,
            hide_index=True
        )
        st.caption("Nur FMP-Daten · Kein RAG · Automatisch generiert")
    except Exception as e:
        st.info(f"Peer-Daten konnten nicht geladen werden: {e}")
```

### Schritt 7.4 — Test

```
AAPL, 2024:
- Tabelle zeigt AAPL (fett) + mind. 2–3 Peers
- Spalten: Ticker, ROIC, Net Margin, CAGR (5J), MOS%
- N/A erlaubt wenn Daten fehlen
```

### Git Commit

```bash
git commit -m "feat: peer comparison table (ROIC, Net Margin, CAGR, MOS%)"
```

---

## MILESTONE 8 — Overview Page Update

**Ziel:** Overview zeigt das neue 3-Score-Modell klar, mit Gewichtungen und Combined Score.

### Schritt 8.1 — Scores-Sektion neu strukturieren

In `_page_overview()`, nach dem Combined Score Banner:

```python
# ── Score-Übersicht ───────────────────────────────────────────────────
st.subheader("📊 Score-Übersicht")
c1, c2, c3, c4 = st.columns(4)

ai = result.get("ai_decision", {})
q_score  = ai.get("quantitative_score", 0)
v_score  = ai.get("valuation_score", 0)    # Falls separat vorhanden
m_score  = ai.get("qualitative_score", 0)
combined = ai.get("overall_score", 0)

c1.metric("Quality Score (40%)",   f"{q_score}/100",  help="ROIC · FCF Yield · Net Margin · CAGR")
c2.metric("Valuation Score (20%)", f"{v_score}/100",  help="MOS · TenCap · PBT")
c3.metric("Moat Score (40%)",      f"{m_score}/100",  help="RAG qualitative Moat-Analyse")
c4.metric("Combined Score",        f"{combined}/100", help="40% · 20% · 40% − Red Flag Penalty")
```

### Schritt 8.2 — Moat Strength konsistent anzeigen

Moat Strength im Overview soll dieselbe sein wie auf der Moat Page.
Sicherstellen dass `ai.get("moat_strength")` direkt aus `moat_analyzer.py` kommt (nicht neu berechnet wird).

### Schritt 8.3 — Entscheidungslogik sichtbar machen

```python
# Zeige welche Regel angewendet wurde
decision = ai.get("decision", "N/A")
flags    = len(ai.get("red_flags", []))
st.caption(
    f"Entscheidungsregel: Combined={combined} | Red Flags={flags} → {decision}"
)
```

### Schritt 8.4 — Test

```
AAPL:
- 4 Metrics sichtbar (Quality, Valuation, Moat, Combined)
- Keine widersprüchlichen Werte (Milestone 3 Voraussetzung)
- Entscheidungs-Caption korrekt
```

### Git Commit

```bash
git commit -m "feat: overview shows 3-score model with weights"
```

---

## MILESTONE 9 — Final Test & Cleanup

**Ziel:** Alles läuft, Code ist sauber, Flowchart aktualisiert.

### Schritt 9.1 — End-to-End Test: AAPL

Gehe jeden Page durch:

| Page             | Erwartetes Verhalten                                |
| ---------------- | --------------------------------------------------- |
| 📈 CAGR          | Tabelle mit Rolling-Perioden                        |
| 🛡️ MOS           | Fair Value + Transparenz-Box für Growth Rate        |
| 💰 Profitability | ROIC, ROE, ROA, Net Margin, FCF Yield               |
| ⏱️ PBT           | Buy Price, Fair Value, FCF Tabelle                  |
| 🤖 AI Moat       | Business Model + Quality Indicator + Scores + Peers |
| 📊 Overview      | 3 Scores + Combined + Entscheidung                  |

### Schritt 9.2 — Test mit anderem Ticker

Teste einen weiteren Ticker (z.B. `MSFT` oder ein europäischer Ticker wie `NOVN.SW`).
Peer-Fallback auf Claude sollte greifen falls FMP leer zurückgibt.

### Schritt 9.3 — Session Limit Test

Setze `MAX_ANALYSES_PER_SESSION = 3` temporär, führe 4 Analysen durch.
→ 4. Analyse soll mit Fehlermeldung stoppen.

### Schritt 9.4 — Flowchart finalisieren

In `valuekit_flowchart.mermaid`:

- Entferne die Nummern ⑤⑥⑦ aus den Boxen (waren interne Entwicklungs-Labels)
- Exportiere PNG/SVG via [mermaid.live](https://mermaid.live) (White Background)
- PNG für Thesis speichern

### Schritt 9.5 — Final Git Commit

```bash
git add .
git commit -m "feat: valuekit-ai MVP complete — all milestones implemented"
git tag v1.0.0-mvp
```

---

## Zusammenfassung: Was wird implementiert

| Flowchart-Element                  | Milestone | Status nach Impl.        |
| ---------------------------------- | --------- | ------------------------ |
| ① Quality Score (ROIC·FCF·NM·CAGR) | M2        | ✅ Implementiert         |
| ② Valuation Score (MOS·PBT)        | M2        | ✅ (war schon teilw. da) |
| ③ Quantitative Fundamentals        | M1+M4     | ✅ Implementiert         |
| ④ Moat Score (RAG)                 | M1        | ✅ Implementiert         |
| ⑤ Peer Vergleichstabelle           | M7        | ✅ Implementiert         |
| ⑥ 3-Quellen Wachstumsrate          | M5        | ✅ Implementiert         |
| ⑦ Data Quality Check               | M6        | ✅ Implementiert         |
| Combined Score (40/20/40)          | M2+M3     | ✅ Implementiert         |
| Entscheidungslogik (BUY/HOLD/PASS) | M3        | ✅ Implementiert         |
| Business Model Beschreibung        | M1        | ✅ Implementiert         |

---

_Letztes Update: Woche 3 · Ziel-Version: Flowchart v3_
