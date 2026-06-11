# Walk-Forward Validation — Design (Phase 20)

> **Source of truth:** this spec was written FROM the implemented code
> (`python/core/walkforward.py`, 2026-06-10). If spec and code ever
> disagree, the code wins. All referenced APIs below are the REAL ones
> (see notes in backtester/orchestrator specs about `side`/`impulse_pct`
> and `stop_loss`/`take_profit` — those apply here too).

## Purpose

Rolling out-of-sample validation on top of the existing bar-by-bar
`Backtester`. Splits history into train/test windows, runs each test
segment OOS, optionally grid-searches params on each train window.
Answers: "does the strategy hold up on data it never saw?"

## Module

`python/core/walkforward.py`

### Window layout

```
|--- train (train_bars) ---|--- test (test_bars) ---|
              |--- train ---|--- test ---|       (step = test_bars)
```

- Index-based bounds, start inclusive / end exclusive; `train_end == test_start`.
- Only full test windows; trailing remainder shorter than `test_bars` discarded.
- Test segments are contiguous and non-overlapping → stitched trades form one
  chronological OOS equity curve.

### Lookahead policy

- Train optimization sees ONLY train bars.
- Each test run gets the last `warmup_bars` of its train window prepended as
  context (Backtester skips warmup bars for entries) — regime/OB state carries
  in, no future bar is ever consumed.

## Real API (verbatim from code)

```python
class WalkForwardRunner:
    def __init__(
        self,
        config:      TradingConfig,
        train_bars:  int = 500,    # must be > warmup_bars
        test_bars:   int = 250,    # must be >= 1; also the roll step
        warmup_bars: int = 60,     # must be >= 1
        param_grid:  Optional[Dict[str, List[float]]] = None,
        # param_grid keys must be existing TradingConfig fields,
        # each with >= 1 candidate value; ValueError otherwise.
    ) -> None: ...

    def run(self, df: pd.DataFrame, symbol: str) -> WalkForwardResult: ...
    def split_windows(self, n_bars: int) -> List[Tuple[int, int, int, int]]: ...
    # returns (train_start, train_end, test_start, test_end) iloc bounds

@dataclass
class WalkForwardWindow:
    window_idx:   int
    train_start:  pd.Timestamp
    train_end:    pd.Timestamp     # inclusive
    test_start:   pd.Timestamp
    test_end:     pd.Timestamp     # inclusive
    params:       Dict[str, float] # overrides applied to test ({} = defaults)
    test_result:  BacktestResult
    train_result: Optional[BacktestResult] = None  # None when no param_grid

@dataclass
class WalkForwardResult:
    symbol:                 str
    timeframe:              str
    train_bars:             int
    test_bars:              int
    window_count:           int
    total_trades:           int
    win_count:              int
    loss_count:             int
    win_rate:               float
    total_r:                float
    avg_r_per_trade:        float
    max_drawdown_r:         float
    sharpe_r:               float
    profit_factor:          float
    pct_profitable_windows: float   # fraction of windows with test total_r > 0
    windows:                List[WalkForwardWindow] = field(default_factory=list)
    trades:                 List[BacktestTrade]     = field(default_factory=list)
    equity_r:               List[float]             = field(default_factory=list)

    def summary(self) -> str: ...   # one-line human-readable
```

## Behaviour rules

1. `run()` on a df too short for one full train+test window logs a warning
   and returns an empty result (`window_count=0`) — it does NOT raise.
2. Optimization: every param combination is backtested on the train window;
   winner = highest `total_r`, ties → first combination in iteration order
   (deterministic). Winner applied to test via `dataclasses.replace` —
   the base config is never mutated.
3. Aggregate metrics use the SAME formulas as `Backtester._compute_result`
   (sample-stdev trade Sharpe, SL-first conservative exits inherited, etc.)
   so WF numbers are directly comparable to plain backtest numbers.
4. Zero trades across all windows → empty metrics but `windows` list and
   `pct_profitable_windows` still populated for inspection.

## Tests

`python/tests/test_walkforward.py` — 26 tests: constructor validation,
split math (overlap/lookahead/remainder), mocked-Backtester integration,
param-grid selection & tie-breaks, aggregation formula parity, zero-trade
and short-data edges, plus an unmocked end-to-end smoke test.

## Future (not implemented)

- Anchored (expanding-train) mode.
- Per-window selection metrics other than total_r (e.g. sharpe_r).
- Pine Script conceptual mirror for TradingView visual validation.
