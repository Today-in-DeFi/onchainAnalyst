# onchainAnalyst

Weekly on-chain DeFi market overview — stablecoin flows, chain TVL, protocol movers.

## Latest Report

- [Weekly DeFi Overview — Feb 23, 2026](reports/weekly_defi_overview_2026-02-23.html) (HTML)
- [Weekly DeFi Overview — Feb 23, 2026](reports/weekly_defi_overview_2026-02-23.md) (Markdown)

## Data Source

All data sourced from [DefiLlama](https://defillama.com/) APIs — stablecoins, chain TVL, DEX volumes, protocol metrics.

## Usage

### Quick Start

```bash
pip install -r requirements.txt
python3 scripts/gather_data.py          # fetches data, saves to data/raw_<today>.json
python3 scripts/gather_data.py 2026-02-24  # specify a date label
```

### Generate a Report with Claude Code

Use the `/report` skill inside Claude Code:

```
/report              # generate report for today
/report 2026-02-24   # generate report for a specific date
```

This runs the data collection script, then uses Claude to write the full analyst report to `reports/weekly_defi_overview_<date>.md`.

## Sections Covered

- **Stablecoin Supply** — issuance, redemptions, and market share shifts
- **Chain Analysis** — TVL, DEX volume, and stablecoin flows by chain
- **Protocol Movers** — biggest gainers and losers by TVL (>=$50M)
- **Summary & Narratives** — analyst commentary on key trends
