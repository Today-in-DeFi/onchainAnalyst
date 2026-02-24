#!/usr/bin/env python3
"""Fetch DeFi data from DefiLlama APIs and save as structured JSON."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://api.llama.fi"
STABLES_BASE = "https://stablecoins.llama.fi"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "onchainAnalyst/1.0"

TOP_CHAINS_FOR_STABLES = [
    "Ethereum", "Tron", "BSC", "Solana", "Base",
    "Hyperliquid", "Arbitrum", "Polygon", "Avalanche",
    "Optimism", "Sui", "Aptos", "Provenance",
]

MIN_PROTOCOL_TVL = 50_000_000  # $50M


def get(url: str, label: str = ""):
    """GET with basic error handling."""
    try:
        r = SESSION.get(url, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        print(f"  WARN: failed to fetch {label or url}: {e}", file=sys.stderr)
        return None


def chart_ts(entry):
    """Extract timestamp from a chart entry (date field may be str or int)."""
    d = entry.get("date", 0)
    return int(d)


def chart_supply(entry):
    """Extract totalCirculating.peggedUSD from a chart entry."""
    circ = entry.get("totalCirculating", {})
    return circ.get("peggedUSD", 0) if isinstance(circ, dict) else 0


def find_7d_ago(chart):
    """Given a chart list, return (last_entry, entry_7d_ago)."""
    if not chart:
        return None, None
    last = chart[-1]
    target_ts = chart_ts(last) - 7 * 86400
    prev = None
    for entry in chart:
        if chart_ts(entry) <= target_ts:
            prev = entry
    if prev is None and len(chart) > 7:
        prev = chart[-8]
    elif prev is None and len(chart) > 1:
        prev = chart[0]
    return last, prev


# ── Stablecoins ──────────────────────────────────────────────────────────

def fetch_stablecoins():
    """Fetch stablecoin list + compute 7d supply changes."""
    print("Fetching stablecoins...")
    data = get(f"{STABLES_BASE}/stablecoins?includePrices=true", "stablecoin list")
    if not data:
        return {}

    coins = data.get("peggedAssets", [])

    # Also fetch the aggregate history to get total supply 7d ago
    history = get(f"{STABLES_BASE}/stablecoincharts/all", "stablecoin history")

    total_now = 0
    total_7d_ago = 0
    results = []

    for coin in coins:
        name = coin.get("name", "")
        symbol = coin.get("symbol", "")
        cid = coin.get("id")

        # Current supply from the peggedUSD circulating field
        circ = coin.get("circulating", {})
        current = circ.get("peggedUSD", 0) if isinstance(circ, dict) else 0
        if current < 100_000:  # skip tiny stables
            continue

        # Get 7d ago supply from chain circulating history
        chains = coin.get("chainCirculating", {})
        supply_7d_ago = 0
        for chain_data in chains.values():
            if isinstance(chain_data, dict):
                circ_hist = chain_data.get("current", {})
                supply_7d_ago += circ_hist.get("peggedUSD", 0)

        # If chainCirculating doesn't give us a different number, use per-coin chart
        if abs(supply_7d_ago - current) < 1:
            supply_7d_ago = current  # fallback: will compute from chart below

        total_now += current

        results.append({
            "id": cid,
            "name": name,
            "symbol": symbol,
            "current_supply": current,
        })

    # Fetch per-coin 7d change from individual charts for top coins
    # Sort by supply first so we only query top ones
    results.sort(key=lambda x: x["current_supply"], reverse=True)
    top_coins = results[:30]

    for coin in top_coins:
        chart = get(
            f"{STABLES_BASE}/stablecoincharts/all?stablecoin={coin['id']}",
            f"chart for {coin['symbol']}"
        )
        if chart and len(chart) > 0:
            last, prev = find_7d_ago(chart)
            current_from_chart = chart_supply(last) if last else 0

            if prev:
                prev_supply = chart_supply(prev)
                if current_from_chart > 0:
                    coin["current_supply"] = current_from_chart
                coin["supply_7d_ago"] = prev_supply
                coin["change_7d_usd"] = current_from_chart - prev_supply
                coin["change_7d_pct"] = (
                    (current_from_chart - prev_supply) / prev_supply * 100
                    if prev_supply > 0 else 0
                )

    # Compute totals from the aggregate chart
    if history and len(history) > 0:
        last_total, prev_total = find_7d_ago(history)
        if last_total:
            total_now = chart_supply(last_total)
        if prev_total:
            total_7d_ago = chart_supply(prev_total)

    return {
        "total_supply": total_now,
        "total_supply_7d_ago": total_7d_ago,
        "total_change_7d_usd": total_now - total_7d_ago,
        "total_change_7d_pct": (
            (total_now - total_7d_ago) / total_7d_ago * 100
            if total_7d_ago > 0 else 0
        ),
        "coins": [c for c in top_coins if "change_7d_usd" in c],
    }


# ── Chain TVL ────────────────────────────────────────────────────────────

def fetch_chains():
    """Fetch chain TVL rankings with 7d change from historical data."""
    print("Fetching chain TVL...")
    data = get(f"{BASE}/v2/chains", "chains")
    if not data:
        return {}

    # Get current TVL per chain, filter to >$10M
    chain_list = []
    for c in data:
        tvl = c.get("tvl", 0)
        if tvl < 10_000_000:
            continue
        chain_list.append({"name": c.get("name", ""), "tvl": tvl})

    chain_list.sort(key=lambda x: x["tvl"], reverse=True)

    # Fetch historical TVL for top chains to compute 7d change
    chains = []
    for c in chain_list[:30]:
        name = c["name"]
        tvl = c["tvl"]
        hist = get(f"{BASE}/v2/historicalChainTvl/{name}", f"history {name}")
        change_7d_pct = 0
        change_7d_usd = 0
        if hist and len(hist) >= 8:
            current_tvl = hist[-1].get("tvl", tvl)
            tvl_7d_ago = hist[-8].get("tvl", current_tvl)
            if tvl_7d_ago > 0:
                change_7d_pct = (current_tvl - tvl_7d_ago) / tvl_7d_ago * 100
                change_7d_usd = current_tvl - tvl_7d_ago
            tvl = current_tvl  # use the more precise historical value

        chains.append({
            "name": name,
            "tvl": tvl,
            "change_7d_pct": change_7d_pct,
            "change_7d_usd": change_7d_usd,
        })

    # Identify top growers and decliners among chains >$200M TVL
    significant = [c for c in chains if c["tvl"] >= 200_000_000]
    gainers = sorted(
        [c for c in significant if c["change_7d_pct"] > 0],
        key=lambda x: x["change_7d_pct"], reverse=True
    )[:10]
    losers = sorted(
        [c for c in significant if c["change_7d_pct"] < 0],
        key=lambda x: x["change_7d_pct"]
    )[:10]

    return {
        "all": chains,
        "top_gainers": gainers,
        "top_losers": losers,
    }


# ── DEX Volumes ──────────────────────────────────────────────────────────

def fetch_dex_volumes():
    """Fetch DEX volumes per chain — 7d and prev 7d."""
    print("Fetching DEX volumes...")
    data = get(f"{BASE}/overview/dexs?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true&dataType=dailyVolume", "dex overview")
    if not data:
        return {}

    # Per-chain volumes from the overview
    chain_volumes = {}
    chains_to_query = [
        "Ethereum", "Solana", "BSC", "Base", "Arbitrum",
        "Polygon", "Avalanche", "Sui", "Optimism", "Hyperliquid",
    ]

    for chain in chains_to_query:
        cdata = get(
            f"{BASE}/overview/dexs/{chain}?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true&dataType=dailyVolume",
            f"dex {chain}"
        )
        if not cdata:
            continue

        chart = cdata.get("totalDataChart", [])
        if not chart or len(chart) < 14:
            continue

        # Sum last 7 entries = current 7d volume
        recent_7d = sum(entry[1] for entry in chart[-7:] if len(entry) >= 2)
        prev_7d = sum(entry[1] for entry in chart[-14:-7] if len(entry) >= 2)

        wow_pct = ((recent_7d - prev_7d) / prev_7d * 100) if prev_7d > 0 else 0

        chain_volumes[chain] = {
            "volume_7d": recent_7d,
            "volume_prev_7d": prev_7d,
            "wow_pct": wow_pct,
        }

    # Sort by 7d volume
    sorted_chains = sorted(chain_volumes.items(), key=lambda x: x[1]["volume_7d"], reverse=True)
    return {name: vol for name, vol in sorted_chains}


# ── Chain Stablecoin Flows ───────────────────────────────────────────────

def fetch_chain_stablecoin_flows():
    """Fetch stablecoin supply per chain — current vs 7d ago."""
    print("Fetching chain stablecoin flows...")
    results = {}

    for chain in TOP_CHAINS_FOR_STABLES:
        data = get(
            f"{STABLES_BASE}/stablecoincharts/{chain}",
            f"stables {chain}"
        )
        if not data or len(data) < 2:
            continue

        last, prev = find_7d_ago(data)
        current = chart_supply(last) if last else 0
        prev_supply = chart_supply(prev) if prev else 0

        if current > 0:
            results[chain] = {
                "stable_supply": current,
                "stable_supply_7d_ago": prev_supply,
                "change_7d_usd": current - prev_supply,
                "change_7d_pct": (
                    (current - prev_supply) / prev_supply * 100
                    if prev_supply > 0 else 0
                ),
            }

    # Sort by supply
    return dict(sorted(results.items(), key=lambda x: x[1]["stable_supply"], reverse=True))


# ── Protocol Movers ──────────────────────────────────────────────────────

def fetch_protocols():
    """Fetch protocols >= $50M TVL, sorted by 7d change."""
    print("Fetching protocols...")
    data = get(f"{BASE}/protocols", "protocols")
    if not data:
        return {}

    filtered = []
    for p in data:
        tvl = p.get("tvl", 0)
        if not tvl or tvl < MIN_PROTOCOL_TVL:
            continue
        change_7d = p.get("change_7d", 0)
        if change_7d is None:
            change_7d = 0

        # Compute dollar change from percentage
        # current = prev * (1 + pct/100), so prev = current / (1 + pct/100)
        # dollar_change = current - prev
        if change_7d != 0:
            prev = tvl / (1 + change_7d / 100)
            dollar_change = tvl - prev
        else:
            dollar_change = 0

        chains = p.get("chains", [])
        chain_str = chains[0] if len(chains) == 1 else f"Multi ({len(chains)})" if len(chains) > 1 else "Unknown"

        filtered.append({
            "name": p.get("name", ""),
            "symbol": p.get("symbol", ""),
            "category": p.get("category", ""),
            "chain": chain_str,
            "chains": chains,
            "tvl": tvl,
            "change_7d_pct": change_7d,
            "change_7d_usd": dollar_change,
        })

    # Gainers and losers by percentage
    gainers = sorted(
        [p for p in filtered if p["change_7d_pct"] > 0],
        key=lambda x: x["change_7d_pct"], reverse=True
    )[:20]
    losers = sorted(
        [p for p in filtered if p["change_7d_pct"] < 0],
        key=lambda x: x["change_7d_pct"]
    )[:20]

    # Biggest absolute movers
    abs_gainers = sorted(
        [p for p in filtered if p["change_7d_usd"] > 0],
        key=lambda x: x["change_7d_usd"], reverse=True
    )[:15]
    abs_losers = sorted(
        [p for p in filtered if p["change_7d_usd"] < 0],
        key=lambda x: x["change_7d_usd"]
    )[:15]

    return {
        "top_gainers_pct": gainers,
        "top_losers_pct": losers,
        "top_gainers_abs": abs_gainers,
        "top_losers_abs": abs_losers,
    }


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": date_str,
        "period": "7d",
        "stablecoins": fetch_stablecoins(),
        "chains": fetch_chains(),
        "dex_volumes": fetch_dex_volumes(),
        "chain_stablecoin_flows": fetch_chain_stablecoin_flows(),
        "protocols": fetch_protocols(),
    }

    out_dir = Path(__file__).resolve().parent.parent / "data"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"raw_{date_str}.json"

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nDone. Saved to {out_path}")
    print(f"  Stablecoins: {len(output['stablecoins'].get('coins', []))} coins tracked")
    print(f"  Chains: {len(output['chains'].get('all', []))} chains")
    print(f"  DEX volumes: {len(output['dex_volumes'])} chains")
    print(f"  Stablecoin flows: {len(output['chain_stablecoin_flows'])} chains")
    print(f"  Protocols: {len(output['protocols'].get('top_gainers_pct', []))} gainers, "
          f"{len(output['protocols'].get('top_losers_pct', []))} losers")


if __name__ == "__main__":
    main()
