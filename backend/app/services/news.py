"""Economic calendar + live financial headlines + deterministic sentiment."""
from __future__ import annotations

import os
import re
import time

import httpx

SYNTHETIC_PREFIXES = ("R_", "1HZ", "BOOM", "CRASH", "STEP", "JD")
CALENDAR_TTL = 300
NEWS_TTL = 300

PAIR_EVENT_CURRENCIES = {
    "xauusd": ["USD"],
    "eurusd": ["EUR", "USD"],
    "gbpusd": ["GBP", "USD"],
    "usdjpy": ["USD", "JPY"],
    "us30": ["USD"],
}

PAIR_HEADLINE_QUERY = {
    "xauusd": '"gold" OR XAUUSD OR "Federal Reserve" OR dollar OR yields',
    "eurusd": 'EURUSD OR euro OR dollar OR ECB OR "Federal Reserve"',
    "gbpusd": 'GBPUSD OR pound OR dollar OR BOE OR "Federal Reserve"',
    "usdjpy": 'USDJPY OR yen OR dollar OR BOJ OR "Federal Reserve"',
    "us30": '"Dow Jones" OR US30 OR "US stocks" OR "Federal Reserve" OR Treasury yields',
}

PAIR_SENTIMENT_TERMS = {
    "xauusd": {
        "bullish": [
            "gold rises", "gold gains", "gold rallies", "safe-haven demand",
            "safe haven demand", "dovish fed", "weaker dollar", "falling yields",
            "lower yields", "rate cuts", "central bank buying",
        ],
        "bearish": [
            "gold falls", "gold drops", "gold slides", "stronger dollar",
            "hawkish fed", "higher yields", "reduced safe-haven demand",
            "reduced safe haven demand", "risk-on", "risk appetite",
        ],
    },
    "eurusd": {
        "bullish": ["stronger euro", "euro strengthens", "ecb hawkish", "weaker dollar", "fed dovish"],
        "bearish": ["weaker euro", "euro weakens", "ecb dovish", "stronger dollar", "fed hawkish"],
    },
    "gbpusd": {
        "bullish": ["stronger pound", "pound strengthens", "boe hawkish", "weaker dollar", "fed dovish"],
        "bearish": ["weaker pound", "pound weakens", "boe dovish", "stronger dollar", "fed hawkish"],
    },
    "usdjpy": {
        "bullish": ["stronger dollar", "fed hawkish", "higher yields", "yen weakens", "boj dovish", "risk-on"],
        "bearish": ["weaker dollar", "fed dovish", "lower yields", "yen strengthens", "safe-haven yen", "risk-off"],
    },
    "us30": {
        "bullish": ["stocks rally", "equities rally", "risk-on", "fed dovish", "soft landing", "strong earnings"],
        "bearish": ["stocks fall", "equities slide", "risk-off", "fed hawkish", "higher yields", "recession fears"],
    },
}

POSITIVE_EVENTS = {
    "USD": ["Non-Farm Payrolls", "GDP", "Retail Sales", "CPI", "Core CPI", "Consumer Sentiment", "ISM Manufacturing"],
    "EUR": ["GDP", "CPI", "ECB Interest Rate", "Retail Sales"],
    "GBP": ["GDP", "CPI", "BOE Rate", "Retail Sales"],
    "JPY": ["GDP", "CPI", "BOJ Policy Rate"],
}
INVERSE_EVENTS = {
    "Unemployment Rate", "Unemployment Claims", "Initial Jobless Claims",
    "Continuing Claims", "Jobless Claims",
}
HIGH_IMPACT_EVENTS = {
    "Non-Farm Payrolls", "Interest Rate Decision", "GDP", "CPI", "Core CPI",
    "ECB Interest Rate", "BOE Rate", "BOJ Policy Rate", "RBA Rate",
    "BOC Rate", "SNB Policy Rate", "RBNZ Rate",
}

_calendar_cache: dict[str, tuple[float, list[dict]]] = {}
_headline_cache: dict[str, tuple[float, list[dict], dict]] = {}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def is_synthetic(pair: str) -> bool:
    return pair.upper().startswith(SYNTHETIC_PREFIXES)


def _headline_provider() -> str:
    chosen = (_env("HEADLINE_PROVIDER", "") or "").lower()
    if chosen:
        return chosen
    if _env("MARKETAUX_API_KEY"):
        return "marketaux"
    if _env("NEWSAPI_KEY"):
        return "newsapi"
    return "none"


def _impact_label(value) -> str:
    try:
        n = int(value)
    except Exception:
        n = 0
    return {3: "high", 2: "medium", 1: "low"}.get(n, "low")


def _extract_number(value) -> float | None:
    if value in (None, "", "-"):
        return None
    s = str(value)
    number_str = re.search(r"[-+]?\d*\.?\d+", s)
    if not number_str:
        return None
    n = float(number_str.group())
    if "K" in s or "k" in s:
        return n * 1000
    if "M" in s or "m" in s:
        return n * 1000000
    return n


def _headline_status(provider: str, key_present: bool, error: str | None) -> str:
    if provider == "none":
        return "disabled"
    if not key_present:
        return "disabled"
    return "error" if error else "live"


def _calendar_events(pair: str) -> list[dict]:
    now = time.time()
    cached = _calendar_cache.get(pair)
    if cached and now - cached[0] < CALENDAR_TTL:
        return cached[1]
    api_key = _env("TRADING_ECONOMICS_API_KEY")
    if not api_key or is_synthetic(pair):
        return []

    currencies = ",".join(PAIR_EVENT_CURRENCIES.get(pair, ["USD"]))
    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.get(
                "https://api.tradingeconomics.com/calendar",
                params={
                    "c": api_key,
                    "f": "json",
                    "importance": "2",
                    "countries": "",
                    "calendar": "",
                    "currency": currencies,
                },
            )
            resp.raise_for_status()
            raw = resp.json()
    except Exception:
        return []

    events: list[dict] = []
    now_ts = int(time.time())
    for item in raw if isinstance(raw, list) else []:
        date_str = item.get("Date") or item.get("date")
        currency = item.get("Currency") or item.get("currency")
        title = item.get("Event") or item.get("event") or item.get("Category") or item.get("category")
        if not date_str or not currency or not title:
            continue
        try:
            ts = int(time.mktime(time.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S")))
        except Exception:
            try:
                ts = int(time.mktime(time.strptime(date_str[:19].replace(" ", "T"), "%Y-%m-%dT%H:%M:%S")))
            except Exception:
                continue
        minutes_away = int((ts - now_ts) / 60)
        if minutes_away < -120 or minutes_away > 24 * 60:
            continue
        impact = _impact_label(item.get("Importance") or item.get("importance"))
        events.append(
            {
                "title": str(title),
                "currency": str(currency),
                "impact": impact,
                "minutes_away": minutes_away,
                "release_ts": ts,
                "provider": "tradingeconomics",
                "actual": item.get("Actual") or item.get("actual"),
                "forecast": item.get("Forecast") or item.get("forecast"),
                "previous": item.get("Previous") or item.get("previous"),
            }
        )
    events.sort(key=lambda x: abs(x["minutes_away"]))
    _calendar_cache[pair] = (now, events)
    return events


def _normalize_marketaux_article(item: dict) -> dict | None:
    title = item.get("title")
    if not title:
        return None
    source = item.get("source") or item.get("domain") or item.get("source_name")
    entities = item.get("entities") if isinstance(item.get("entities"), list) else []
    sentiment_values = [
        float(ent.get("sentiment_score"))
        for ent in entities
        if isinstance(ent, dict) and ent.get("sentiment_score") is not None
    ]
    return {
        "title": title,
        "description": item.get("description") or item.get("snippet"),
        "url": item.get("url"),
        "source": source,
        "published_at": item.get("published_at"),
        "provider": "marketaux",
        "entities": entities,
        "provider_sentiment": round(sum(sentiment_values) / len(sentiment_values), 3) if sentiment_values else None,
    }


def _fetch_marketaux_articles(pair: str) -> tuple[list[dict], str | None]:
    api_key = _env("MARKETAUX_API_KEY")
    if not api_key or is_synthetic(pair):
        return [], None
    query = PAIR_HEADLINE_QUERY.get(pair, pair)
    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.get(
                "https://api.marketaux.com/v1/news/all",
                params={
                    "api_token": api_key,
                    "search": query,
                    "language": "en",
                    "limit": 3,
                    "page": 1,
                    "filter_entities": "true",
                },
            )
            resp.raise_for_status()
            raw = resp.json()
    except Exception as exc:
        return [], str(exc)

    data = raw.get("data") if isinstance(raw, dict) else None
    items = data if isinstance(data, list) else raw.get("articles", []) if isinstance(raw, dict) else []
    articles = [a for item in items[:3] if (a := _normalize_marketaux_article(item))]
    return articles, None


def _fetch_newsapi_articles(pair: str) -> tuple[list[dict], str | None]:
    api_key = _env("NEWSAPI_KEY")
    if not api_key or is_synthetic(pair):
        return [], None
    query = PAIR_HEADLINE_QUERY.get(pair, pair)
    try:
        with httpx.Client(timeout=12.0, headers={"X-Api-Key": api_key}) as client:
            resp = client.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "language": "en",
                    "pageSize": 3,
                    "sortBy": "publishedAt",
                    "searchIn": "title,description",
                },
            )
            resp.raise_for_status()
            raw = resp.json()
    except Exception as exc:
        return [], str(exc)

    articles = []
    for item in raw.get("articles", [])[:3]:
        title = item.get("title")
        if not title:
            continue
        articles.append(
            {
                "title": title,
                "description": item.get("description"),
                "url": item.get("url"),
                "source": (item.get("source") or {}).get("name"),
                "published_at": item.get("publishedAt"),
                "provider": "newsapi",
                "entities": [],
                "provider_sentiment": None,
            }
        )
    return articles, None


def _headline_articles(pair: str) -> tuple[list[dict], dict]:
    now = time.time()
    cached = _headline_cache.get(pair)
    provider = _headline_provider()
    if cached and now - cached[0] < NEWS_TTL and cached[2].get("selected_headline_provider") == provider:
        return cached[1], cached[2]

    status = {
        "calendar": "live" if _env("TRADING_ECONOMICS_API_KEY") else "disabled",
        "headlines": "disabled",
        "news": "disabled",
        "selected_headline_provider": provider,
    }
    articles: list[dict] = []
    error: str | None = None

    if provider == "marketaux":
        articles, error = _fetch_marketaux_articles(pair)
        status["headlines"] = _headline_status(provider, bool(_env("MARKETAUX_API_KEY")), error)
    elif provider == "newsapi":
        articles, error = _fetch_newsapi_articles(pair)
        status["headlines"] = _headline_status(provider, bool(_env("NEWSAPI_KEY")), error)
    elif provider == "none":
        status["headlines"] = "disabled"
    else:
        status["headlines"] = "error"
        error = f"unknown provider: {provider}"

    status["news"] = status["headlines"]
    if error:
        status["headline_error"] = error
    _headline_cache[pair] = (now, articles, status)
    return articles, status


def _pair_delta_from_currency(pair: str, currency: str, stronger: bool) -> float:
    mapping = {
        "xauusd": ("XAU", "USD"),
        "eurusd": ("EUR", "USD"),
        "gbpusd": ("GBP", "USD"),
        "usdjpy": ("USD", "JPY"),
    }
    base_quote = mapping.get(pair)
    if not base_quote:
        return 0.0
    base, quote = base_quote
    if currency == base:
        return 0.55 if stronger else -0.55
    if currency == quote:
        return -0.55 if stronger else 0.55
    return 0.0


def _is_event_better(title: str, observed: float, baseline: float) -> bool:
    if any(name.lower() in title.lower() for name in INVERSE_EVENTS):
        return observed < baseline
    return observed > baseline


def _event_driver(pair: str, event: dict) -> tuple[float, str] | None:
    title = str(event.get("title") or "")
    currency = str(event.get("currency") or "")
    actual = _extract_number(event.get("actual"))
    forecast = _extract_number(event.get("forecast"))
    previous = _extract_number(event.get("previous"))
    observed = actual if actual is not None else forecast
    baseline = forecast if actual is not None else previous
    if observed is None or baseline is None or not currency:
        return None

    stronger = _is_event_better(title, observed, baseline)
    base = 0.55 if any(high.lower() in title.lower() for high in HIGH_IMPACT_EVENTS) else 0.35
    delta = _pair_delta_from_currency(pair, currency, stronger) * (base / 0.55)
    if delta == 0:
        return None
    direction = "stronger" if stronger else "weaker"
    return delta, f"{currency} {direction} after {title}"


def _headline_score(pair: str, article: dict) -> tuple[float, list[str]]:
    text = f"{article.get('title') or ''} {article.get('description') or ''}".lower()
    if not text.strip():
        return 0.0, []

    score = 0.0
    drivers: list[str] = []
    rules = PAIR_SENTIMENT_TERMS.get(pair, {"bullish": [], "bearish": []})
    for term in rules["bullish"]:
        if term in text:
            score += 0.32
            drivers.append(term)
    for term in rules["bearish"]:
        if term in text:
            score -= 0.32
            drivers.append(term)

    provider_sentiment = article.get("provider_sentiment")
    if provider_sentiment is not None:
        try:
            score += max(-0.25, min(0.25, float(provider_sentiment)))
            drivers.append(f"provider sentiment {float(provider_sentiment):+.2f}")
        except Exception:
            pass

    return score, drivers[:3]


def _movement_explainer(pair: str, label: str, drivers: list[str], headlines: int, events: int) -> str:
    market = pair.upper()
    if not drivers and headlines == 0 and events == 0:
        return f"No live macro drivers found for {market} yet."
    if not drivers:
        return f"Macro tone for {market} is {label} from current events and recent article flow."
    lead = drivers[0]
    direction = "bullish" if label == "bullish" else "bearish" if label == "bearish" else "mixed"
    return f"{market} macro tone is {direction}, led by {lead}."


def _score_context(pair: str, articles: list[dict], events: list[dict]) -> dict:
    score = 0.0
    drivers: list[str] = []

    for article in articles[:3]:
        delta, article_drivers = _headline_score(pair, article)
        if delta == 0:
            continue
        score += delta
        if article_drivers:
            source = article.get("source") or article.get("provider") or "headline"
            drivers.append(f"{source}: {article_drivers[0]}")

    recent_events = [e for e in events if -120 <= int(e.get("minutes_away", 999999)) <= 60]
    for event in recent_events[:2]:
        result = _event_driver(pair, event)
        if not result:
            continue
        delta, driver = result
        score += delta
        drivers.append(driver)

    score = max(-1.0, min(1.0, round(score, 3)))
    if score >= 0.2:
        label = "bullish"
    elif score <= -0.2:
        label = "bearish"
    else:
        label = "neutral"

    magnitude = abs(score)
    confidence = "high" if magnitude >= 0.65 else "medium" if magnitude >= 0.3 else "low"
    drivers = drivers[:3]
    return {
        "score": score,
        "label": label,
        "confidence": confidence,
        "drivers": drivers,
        "movement_explainer": _movement_explainer(pair, label, drivers, len(articles), len(recent_events)),
        "headline_count": len(articles),
        "event_count": len(recent_events),
        "updated_at": int(time.time()),
    }


def context_for(pair: str, window_minutes: int = 30) -> dict:
    if os.getenv("NEWS_DEMO") == "1" and not is_synthetic(pair):
        event = {
            "title": "US CPI (high impact)",
            "currency": "USD",
            "impact": "high",
            "minutes_away": 24,
            "provider": "demo",
            "veto_active": True,
        }
        sentiment = {
            "score": -0.55 if pair == "xauusd" else 0.0,
            "label": "bearish" if pair == "xauusd" else "neutral",
            "confidence": "medium",
            "drivers": ["USD event risk"],
            "movement_explainer": f"Demo macro risk is active for {pair.upper()}.",
            "headline_count": 0,
            "event_count": 1,
            "updated_at": int(time.time()),
        }
        return {
            "event": event,
            "articles": [],
            "sentiment": sentiment,
            "provider_status": {
                "calendar": "demo",
                "headlines": "demo",
                "news": "demo",
                "selected_headline_provider": "demo",
            },
            "updated_at": int(time.time()),
        }

    events = _calendar_events(pair)
    articles, provider_status = _headline_articles(pair)
    veto_event = next((e for e in events if e["impact"] == "high" and 0 <= e["minutes_away"] <= window_minutes), None)
    next_event = veto_event or (events[0] if events else None)
    if next_event is not None:
        next_event = dict(next_event)
        next_event["veto_active"] = bool(veto_event and next_event["title"] == veto_event["title"])
        next_event["articles"] = articles

    return {
        "event": next_event,
        "articles": articles,
        "sentiment": _score_context(pair, articles, events),
        "provider_status": provider_status,
        "updated_at": int(time.time()),
    }


def upcoming_for(pair: str, window_minutes: int = 30) -> dict | None:
    ctx = context_for(pair, window_minutes=window_minutes)
    event = ctx.get("event")
    if not event or not event.get("veto_active"):
        return None
    return event
