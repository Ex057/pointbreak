"""Pattern grouping, roles, and education copy.

Single source of truth for:
- group membership
- role (trigger / warning / context)
- human-facing education strings
"""
from __future__ import annotations

from collections import Counter

GROUPS = [
    {
        "key": "reversal_bullish",
        "label": "Reversal — Bullish",
        "color": "#1D9E75",
        "blurb": "Bullish reversal patterns suggest sellers may be exhausted and buyers are taking control near support or demand.",
    },
    {
        "key": "reversal_bearish",
        "label": "Reversal — Bearish",
        "color": "#E24B4A",
        "blurb": "Bearish reversal patterns suggest buyers may be exhausted and sellers are taking control near resistance or supply.",
    },
    {
        "key": "exhaustion_indecision",
        "label": "Exhaustion / Indecision",
        "color": "#D97706",
        "blurb": "These patterns are warnings and context clues. They can warn of hesitation or exhaustion, but they are not standalone entries.",
    },
    {
        "key": "continuation_momentum",
        "label": "Continuation / Momentum",
        "color": "#2563EB",
        "blurb": "Momentum and continuation patterns suggest an existing move may continue after brief consolidation or aggressive participation.",
    },
]


def _edu(meaning: str, story: str, valid_when: str, watch_out: str) -> dict[str, str]:
    return {
        "meaning": meaning,
        "story": story,
        "valid_when": valid_when,
        "watch_out": watch_out,
    }


PATTERN_META = {
    "Bullish Engulfing": {
        "group": "reversal_bullish",
        "role": "trigger",
        "education": _edu(
            "Bullish takeover",
            "A strong bullish candle completely swallows the prior bearish body, showing buyers seized control.",
            "At support, demand, discount, or after a sweep low with follow-through.",
            "Weak if it prints mid-range or straight into nearby resistance.",
        ),
    },
    "Bearish Engulfing": {
        "group": "reversal_bearish",
        "role": "trigger",
        "education": _edu(
            "Bearish takeover",
            "A strong bearish candle completely swallows the prior bullish body, showing sellers seized control.",
            "At resistance, supply, premium, or after a sweep high with follow-through.",
            "Weak if it prints mid-range or straight into nearby support.",
        ),
    },
    "Bullish Harami": {
        "group": "reversal_bullish",
        "role": "context",
        "education": _edu(
            "Bullish hesitation after a selloff",
            "A small inside body hints that bearish pressure is slowing, but it does not prove reversal by itself.",
            "When confirmed by a later break up from support or demand.",
            "Too weak to use alone as an entry trigger.",
        ),
    },
    "Bearish Harami": {
        "group": "reversal_bearish",
        "role": "context",
        "education": _edu(
            "Bearish hesitation after a rally",
            "A small inside body hints that bullish pressure is slowing, but it does not prove reversal by itself.",
            "When confirmed by a later break down from resistance or supply.",
            "Too weak to use alone as an entry trigger.",
        ),
    },
    "Piercing Line": {
        "group": "reversal_bullish",
        "role": "trigger",
        "education": _edu(
            "Bullish recovery",
            "Buyers sharply recover after a bearish candle and close back through a meaningful part of it.",
            "After a downside sweep or at demand with follow-through.",
            "Weaker than an engulfing if resistance is very close.",
        ),
    },
    "Dark Cloud Cover": {
        "group": "reversal_bearish",
        "role": "trigger",
        "education": _edu(
            "Bearish rejection",
            "Sellers sharply reject a bullish move and close back through a meaningful part of the prior candle.",
            "After an upside sweep or at supply with follow-through.",
            "Weaker than an engulfing if support is very close.",
        ),
    },
    "Tweezers Bottom": {
        "group": "reversal_bullish",
        "role": "context",
        "education": _edu(
            "Repeated defense of a low",
            "Two candles reject roughly the same low, hinting buyers are defending that level.",
            "When combined with a sweep and reclaim or a strong bullish trigger.",
            "Equal lows alone can also become liquidity, so do not enter on this alone.",
        ),
    },
    "Tweezers Top": {
        "group": "reversal_bearish",
        "role": "context",
        "education": _edu(
            "Repeated defense of a high",
            "Two candles reject roughly the same high, hinting sellers are defending that level.",
            "When combined with a sweep and reclaim or a strong bearish trigger.",
            "Equal highs alone can also become liquidity, so do not enter on this alone.",
        ),
    },
    "Morning Star": {
        "group": "reversal_bullish",
        "role": "trigger",
        "education": _edu(
            "Three-candle bullish reversal",
            "A selloff pauses, prints hesitation, then buyers follow through with a decisive recovery candle.",
            "Best after a sweep into demand or a key support zone.",
            "Avoid using it if the final candle runs directly into nearby resistance.",
        ),
    },
    "Evening Star": {
        "group": "reversal_bearish",
        "role": "trigger",
        "education": _edu(
            "Three-candle bearish reversal",
            "A rally pauses, prints hesitation, then sellers follow through with a decisive rejection candle.",
            "Best after a sweep into supply or a key resistance zone.",
            "Avoid using it if the final candle runs directly into nearby support.",
        ),
    },
    "Hammer": {
        "group": "reversal_bullish",
        "role": "trigger",
        "education": _edu(
            "Bullish wick rejection",
            "The long lower wick shows sellers pushed price down but could not keep it there.",
            "At support or after a sweep low with confirmation.",
            "A hammer in the middle of nowhere is only noise.",
        ),
    },
    "Inverted Hammer": {
        "group": "reversal_bullish",
        "role": "context",
        "education": _edu(
            "Bullish probe upward",
            "Buyers tested higher prices after a selloff, but still need follow-through to prove reversal.",
            "Useful when later confirmed by structure break or strong bullish candle.",
            "Not reliable enough alone to be the entry trigger.",
        ),
    },
    "Hanging Man": {
        "group": "reversal_bearish",
        "role": "trigger",
        "education": _edu(
            "Bearish wick warning after a rally",
            "The long lower wick shows sellers appeared under price during an up move.",
            "At resistance or after a sweep high with confirmation.",
            "Weak if there is no bearish follow-through.",
        ),
    },
    "Shooting Star": {
        "group": "reversal_bearish",
        "role": "trigger",
        "education": _edu(
            "Bearish wick rejection",
            "The long upper wick shows buyers pushed price higher but could not hold it.",
            "At resistance or after a sweep high with confirmation.",
            "A lone shooting star inside chop is not enough.",
        ),
    },
    "Bullish Kicker": {
        "group": "continuation_momentum",
        "role": "trigger",
        "education": _edu(
            "Aggressive bullish shift",
            "Price gaps or violently flips higher, showing sudden buyer aggression.",
            "When aligned with trend or after a major liquidity event.",
            "Can be extended already, so avoid chasing far from structure.",
        ),
    },
    "Bearish Kicker": {
        "group": "continuation_momentum",
        "role": "trigger",
        "education": _edu(
            "Aggressive bearish shift",
            "Price gaps or violently flips lower, showing sudden seller aggression.",
            "When aligned with trend or after a major liquidity event.",
            "Can be extended already, so avoid chasing far from structure.",
        ),
    },
    "White Marubozu": {
        "group": "continuation_momentum",
        "role": "context",
        "education": _edu(
            "Strong bullish commitment",
            "A full-bodied bullish candle with little wick shows strong directional intent.",
            "As momentum confirmation inside an already-valid bullish setup.",
            "Not ideal as a late chase entry after an extended move.",
        ),
    },
    "Black Marubozu": {
        "group": "continuation_momentum",
        "role": "context",
        "education": _edu(
            "Strong bearish commitment",
            "A full-bodied bearish candle with little wick shows strong directional intent.",
            "As momentum confirmation inside an already-valid bearish setup.",
            "Not ideal as a late chase entry after an extended move.",
        ),
    },
    "Three White Soldiers": {
        "group": "continuation_momentum",
        "role": "trigger",
        "education": _edu(
            "Bullish march higher",
            "Three strong bullish candles show sustained buyer control.",
            "Best when emerging from a base or after reclaiming structure.",
            "Often late if it appears after too much distance has already been covered.",
        ),
    },
    "Three Black Crows": {
        "group": "continuation_momentum",
        "role": "trigger",
        "education": _edu(
            "Bearish march lower",
            "Three strong bearish candles show sustained seller control.",
            "Best when emerging from a base or after breaking structure lower.",
            "Often late if it appears after too much distance has already been covered.",
        ),
    },
    "Doji": {
        "group": "exhaustion_indecision",
        "role": "warning",
        "education": _edu(
            "Indecision",
            "Open and close are nearly the same, which signals hesitation rather than control.",
            "As a warning near target or major levels, followed by a real trigger.",
            "Never treat a plain Doji as an entry by itself.",
        ),
    },
    "Dragonfly Doji": {
        "group": "exhaustion_indecision",
        "role": "warning",
        "education": _edu(
            "Bullish rejection warning",
            "A deep lower rejection can hint that sellers failed, but it is still a warning first.",
            "Near support, demand, or as a profit-protection warning against shorts.",
            "Needs confirmation; it is not a standalone entry trigger here.",
        ),
    },
    "Gravestone Doji": {
        "group": "exhaustion_indecision",
        "role": "warning",
        "education": _edu(
            "Bearish rejection warning",
            "A deep upper rejection can hint that buyers failed, but it is still a warning first.",
            "Near resistance, supply, or as a profit-protection warning against longs.",
            "Needs confirmation; it is not a standalone entry trigger here.",
        ),
    },
    "Long-Legged Doji": {
        "group": "exhaustion_indecision",
        "role": "warning",
        "education": _edu(
            "Wide indecision",
            "Both sides pushed price around but neither side kept control by the close.",
            "As a warning that volatility is high and conviction is unclear.",
            "This is context only, not an entry trigger.",
        ),
    },
    "High Wave": {
        "group": "exhaustion_indecision",
        "role": "warning",
        "education": _edu(
            "Exhaustion and instability",
            "Large upper and lower wicks show unstable control and possible exhaustion.",
            "Near targets or key HTF levels as a trade-management warning.",
            "Not valid as an entry trigger; use it to tighten judgment, not to chase.",
        ),
    },
    "Spinning Top": {
        "group": "exhaustion_indecision",
        "role": "warning",
        "education": _edu(
            "Small-body hesitation",
            "A small body with wicks on both sides suggests pause and indecision.",
            "Useful as a caution sign near major levels when combined with structure.",
            "Too weak to trigger an entry on its own.",
        ),
    },
}


_DEFAULT_META = {
    "group": "exhaustion_indecision",
    "role": "context",
    "education": _edu(
        "Context only",
        "This candle can add context, but the system does not trust it as a trade trigger by default.",
        "When it aligns with structure, location, and a stronger confirmation pattern.",
        "Unknown or unmapped patterns must never auto-promote themselves into entry triggers.",
    ),
}


def meta_for(name: str) -> dict:
    return PATTERN_META.get(name, _DEFAULT_META)


def stamp(pattern: dict) -> dict:
    meta = meta_for(pattern.get("name", ""))
    pattern["group"] = meta["group"]
    pattern["role"] = meta["role"]
    pattern["education"] = dict(meta["education"])
    return pattern


# =============================================================================
#  CHART (structure) PATTERNS — separate catalog, grouped bullish/bearish/range
# =============================================================================
from .chart_patterns import CHART_PATTERN_CATEGORY  # noqa: E402

CHART_GROUPS = [
    {
        "key": "bullish",
        "label": "Bullish structures",
        "color": "#1D9E75",
        "blurb": "Multi-bar structures that resolve upward on a confirmed breakout — reversals off support and continuations after a pause.",
    },
    {
        "key": "bearish",
        "label": "Bearish structures",
        "color": "#E24B4A",
        "blurb": "Multi-bar structures that resolve downward on a confirmed breakdown — reversals off resistance and continuations lower.",
    },
    {
        "key": "range",
        "label": "Range / bilateral",
        "color": "#64748B",
        "blurb": "Neutral structures (symmetrical triangles, channels, rectangles, broadening). Direction is unknown until price breaks a boundary.",
    },
]


def _cedu(meaning: str, story: str) -> dict[str, str]:
    return _edu(
        meaning, story,
        "Confirmed only when price CLOSES beyond the pattern boundary (neckline / trendline / rail).",
        "A forming pattern is a watch item, not an entry. Failed breakouts and thin structures are common.",
    )


CHART_PATTERN_META = {
    "Double Bottom": _cedu("Two roughly equal lows — a bullish reversal.",
                            "Sellers failed twice at the same floor; a close above the middle high signals buyers took over."),
    "Triple Bottom": _cedu("Three equal lows — a stronger bullish reversal.",
                            "A floor defended three times; breaking the resistance projects the range height up."),
    "Inverse Head & Shoulders": _cedu("Low-lower-low-higher — a bullish reversal.",
                            "A deep middle low (head) between two higher lows (shoulders); a close above the neckline confirms."),
    "Ascending Triangle": _cedu("Flat resistance, rising support — bullish.",
                            "Buyers keep lifting the lows into a ceiling; a close above it usually continues up."),
    "Falling Wedge": _cedu("Two down-sloping, converging lines — bullish.",
                            "Selling momentum narrows; a break above the upper line often snaps back up."),
    "Bull Flag": _cedu("Sharp up move then a small drift down — bullish continuation.",
                            "A strong flagpole then an orderly pullback; a break up projects another pole length."),
    "Bullish Pennant": _cedu("Up flagpole then a small symmetrical coil — bullish continuation.",
                            "Same as a bull flag but the consolidation converges to a point before breaking up."),
    "Rounding Bottom": _cedu("A smooth U-shape — a slow bullish reversal.",
                            "Sentiment turns gradually from down to up; a break above the rim confirms."),
    "Cup & Handle": _cedu("A rounded base then a small handle — bullish continuation.",
                            "A U-shaped cup then a shallow pullback (handle) near the rim; a break of the rim runs."),
    "Double Top": _cedu("Two roughly equal highs — a bearish reversal.",
                            "Buyers failed twice at the same ceiling; a close below the middle low signals sellers took over."),
    "Triple Top": _cedu("Three equal highs — a stronger bearish reversal.",
                            "A ceiling rejected three times; breaking the support projects the range height down."),
    "Head & Shoulders": _cedu("High-higher-high-lower — a bearish reversal.",
                            "A tall middle high (head) between two lower highs (shoulders); a close below the neckline confirms."),
    "Descending Triangle": _cedu("Flat support, falling resistance — bearish.",
                            "Sellers keep pressing the highs into a floor; a close below it usually continues down."),
    "Rising Wedge": _cedu("Two up-sloping, converging lines — bearish.",
                            "Buying momentum narrows; a break below the lower line often rolls over."),
    "Bear Flag": _cedu("Sharp down move then a small drift up — bearish continuation.",
                            "A down flagpole then an orderly bounce; a break down projects another pole length."),
    "Bearish Pennant": _cedu("Down flagpole then a small symmetrical coil — bearish continuation.",
                            "Same as a bear flag but the consolidation converges before breaking down."),
    "Rounding Top": _cedu("A smooth inverted-U — a slow bearish reversal.",
                            "Sentiment turns gradually from up to down; a break below the rim confirms."),
    "Symmetrical Triangle": _cedu("Falling highs, rising lows — neutral until it breaks.",
                            "A coil of indecision; trade the direction of the boundary that price closes beyond."),
    "Rectangle": _cedu("Flat highs and flat lows — a range.",
                            "Price oscillates between horizontal support and resistance; the break sets direction."),
    "Ascending Channel": _cedu("Parallel rising rails — an up-trend channel.",
                            "Price climbs between two parallel lines; watch for a break of the lower rail."),
    "Descending Channel": _cedu("Parallel falling rails — a down-trend channel.",
                            "Price falls between two parallel lines; watch for a break of the upper rail."),
    "Broadening / Megaphone": _cedu("Rising highs and falling lows — expanding volatility.",
                            "A widening range of indecision; erratic and hard to trade until it resolves."),
}


def _chart_meta(name: str) -> dict:
    edu = CHART_PATTERN_META.get(name)
    category = CHART_PATTERN_CATEGORY.get(name, "range")
    role = "trigger" if category in ("bullish", "bearish") else "context"
    return {"group": category, "role": role, "education": edu or _DEFAULT_META["education"]}


def stamp_chart(pattern: dict) -> dict:
    meta = _chart_meta(pattern.get("name", ""))
    pattern["role"] = meta["role"]
    pattern["education"] = dict(meta["education"])
    return pattern


def grouped_chart_catalog(confirmed_detections: list[dict]) -> list[dict]:
    counts = Counter()
    group_counts = Counter()
    for det in confirmed_detections:
        if not det.get("status") == "confirmed":
            continue
        name = det.get("name")
        counts[name] += 1
        group_counts[CHART_PATTERN_CATEGORY.get(name, "range")] += 1

    out = []
    for group in CHART_GROUPS:
        names = [n for n, cat in CHART_PATTERN_CATEGORY.items() if cat == group["key"]]
        patterns = [{
            "name": name,
            "role": _chart_meta(name)["role"],
            "education": dict(_chart_meta(name)["education"]),
            "count_on_chart": counts.get(name, 0),
        } for name in names]
        out.append({**group, "count_on_chart": group_counts.get(group["key"], 0),
                    "patterns": patterns})
    return out


def grouped_catalog(available_names: list[str], confirmed_detections: list[dict]) -> list[dict]:
    counts = Counter()
    group_counts = Counter()
    for det in confirmed_detections:
        if not det.get("confirmed"):
            continue
        name = det.get("name")
        counts[name] += 1
        group_counts[det.get("group") or meta_for(name)["group"]] += 1

    out = []
    for group in GROUPS:
        names = [name for name in available_names if meta_for(name)["group"] == group["key"]]
        patterns = []
        for name in names:
            meta = meta_for(name)
            patterns.append({
                "name": name,
                "role": meta["role"],
                "education": dict(meta["education"]),
                "count_on_chart": counts.get(name, 0),
            })
        out.append({
            **group,
            "count_on_chart": group_counts.get(group["key"], 0),
            "patterns": patterns,
        })
    return out
