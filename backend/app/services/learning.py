"""Derived setup-quality learner for the AI Analyst.

The deterministic planner remains authoritative. This module builds a rolling
history dataset from the setup journal and closed ledger, retrains a small
probability model daily, and returns advisory scores only.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from threading import Lock
from time import time
from typing import Any

from ..db import repo

MODEL_KIND = "setup_quality_v1"
TRAIN_WINDOW_SAMPLES = 200
MIN_SAMPLE_SIZE = 12
MIN_HOLDOUT_SIZE = 4
RETRAIN_SECONDS = 24 * 60 * 60
PAIR_SCOPE = "xauusd"
# Counterfactual ("what-if") replays of rejected setups enrich the model, but
# must stay a minority so they can't swamp the real taken-trade distribution.
COUNTERFACTUAL_CAP = 120
_LOCK = Lock()


@dataclass
class Sample:
    ts: int
    pair: str
    trader_mode: str
    side: str | None
    source: str | None
    outcome: str
    fill_label: int
    target_label: int
    retire_label: int
    stop_points: float | None = None
    target_points: float | None = None
    rr: float | None = None

    def feature_key(self) -> str:
        return "|".join(
            [
                self.trader_mode or "unknown",
                self.source or "unknown",
                self.side or "unknown",
            ]
        )


def ensure_current_model(pair: str, force_refresh: bool = False) -> dict:
    if pair != PAIR_SCOPE:
        return _disabled_status(pair, "Learning is scoped to XAU/USD in this first version.")
    with _LOCK:
        active = repo.latest_learning_model(pair, status="promoted")
        latest = repo.latest_learning_model(pair)
        now_ts = int(time())
        if (
            not force_refresh
            and active is not None
            and int(active.get("trained_at") or 0) + RETRAIN_SECONDS > now_ts
        ):
            return learning_status(pair)
        if (
            not force_refresh
            and active is None
            and latest is not None
            and int(latest.get("trained_at") or 0) + RETRAIN_SECONDS > now_ts
        ):
            return learning_status(pair)
        return retrain(pair)


def retrain(pair: str) -> dict:
    samples = build_samples(pair)[:TRAIN_WINDOW_SAMPLES]
    samples = sorted(samples, key=lambda item: item.ts)
    now_ts = int(time())
    if len(samples) < MIN_SAMPLE_SIZE:
        summary = _dataset_summary(samples)
        repo.create_learning_model(
            {
                "pair": pair,
                "model_kind": MODEL_KIND,
                "version": _model_version(now_ts),
                "status": "rejected",
                "trained_at": now_ts,
                "window_start": samples[0].ts if samples else None,
                "window_end": samples[-1].ts if samples else None,
                "sample_count": len(samples),
                "holdout_size": 0,
                "artifact": {},
                "metrics": {"reason": "insufficient_samples"},
                "summary": summary,
                "promotion_reason": "Insufficient history to promote a model.",
            }
        )
        return learning_status(pair)

    holdout_size = max(MIN_HOLDOUT_SIZE, len(samples) // 5)
    train = samples[:-holdout_size]
    holdout = samples[-holdout_size:]
    if len(train) < MIN_HOLDOUT_SIZE:
        train = samples
        holdout = []

    artifact = _fit(train)
    baseline = _baseline(train)
    metrics = _evaluate(artifact, baseline, holdout)
    promoted = _should_promote(metrics, len(train), len(holdout))
    repo.create_learning_model(
        {
            "pair": pair,
            "model_kind": MODEL_KIND,
            "version": _model_version(now_ts),
            "status": "promoted" if promoted else "rejected",
            "trained_at": now_ts,
            "window_start": samples[0].ts if samples else None,
            "window_end": samples[-1].ts if samples else None,
            "sample_count": len(samples),
            "holdout_size": len(holdout),
            "artifact": artifact,
            "metrics": metrics,
            "summary": _dataset_summary(samples),
            "promotion_reason": _promotion_reason(promoted, metrics, len(samples)),
        }
    )
    return learning_status(pair)


def learning_status(pair: str) -> dict:
    active = repo.latest_learning_model(pair, status="promoted")
    latest = repo.latest_learning_model(pair)
    if active is None and latest is None:
        return _disabled_status(pair, "No learning model has been trained yet.")
    source = active or latest
    history = repo.learning_model_history(pair, limit=5)
    return {
        "pair": pair,
        "enabled": pair == PAIR_SCOPE,
        "model_kind": MODEL_KIND,
        "active_model_version": (active or {}).get("version"),
        "active_model_status": (active or latest or {}).get("status"),
        "last_retrained": source.get("trained_at"),
        "training_window": {
            "start": source.get("window_start"),
            "end": source.get("window_end"),
        },
        "sample_count": int(source.get("sample_count") or 0),
        "holdout_size": int(source.get("holdout_size") or 0),
        "promotion_result": source.get("status"),
        "promotion_reason": source.get("promotion_reason"),
        "metrics": source.get("metrics") or {},
        "summary": source.get("summary") or {},
        "history": [
            {
                "version": row.get("version"),
                "status": row.get("status"),
                "trained_at": row.get("trained_at"),
                "sample_count": row.get("sample_count"),
                "holdout_size": row.get("holdout_size"),
                "promotion_reason": row.get("promotion_reason"),
                "metrics": row.get("metrics") or {},
            }
            for row in history
        ],
    }


def score_plan(pair: str, plan: dict | None, status: dict | None = None) -> dict | None:
    if pair != PAIR_SCOPE or not plan or not plan.get("side"):
        return None
    status = status or learning_status(pair)
    active = repo.latest_learning_model(pair, status="promoted")
    if active is None:
        return {
            "status": "insufficient_history",
            "quality_score": None,
            "fill_probability": None,
            "target_probability": None,
            "retire_risk": None,
            "model_confidence": "low",
            "segment_matched": False,
            "model_version": status.get("active_model_version"),
            "training_sample_count": status.get("sample_count", 0),
        }
    artifact = active.get("artifact") or {}
    segment_key = _plan_segment_key(plan)
    segments = artifact.get("segments") or {}
    segment_matched = segment_key in segments
    segment = segments.get(segment_key) or artifact.get("global") or {}
    fill_probability = round(float(segment.get("fill_probability") or 0.0), 3)
    target_probability = round(float(segment.get("target_probability") or 0.0), 3)
    retire_risk = round(float(segment.get("retire_risk") or 0.0), 3)
    quality = _quality_score(fill_probability, target_probability, retire_risk)
    count = int(segment.get("count") or 0)
    confidence = "high" if count >= 16 else "medium" if count >= 8 else "low"
    return {
        "status": "ready",
        "quality_score": quality,
        "fill_probability": fill_probability,
        "target_probability": target_probability,
        "retire_risk": retire_risk,
        "model_confidence": confidence,
        "segment_matched": segment_matched,
        "model_version": active.get("version"),
        "training_sample_count": status.get("sample_count", 0),
        "segment_count": count,
        "segment_key": segment_key,
        "history_window": status.get("training_window"),
    }


def build_samples(pair: str) -> list[Sample]:
    journal = repo.list_setup_journal(pair, limit=600)
    closed = repo.closed_ledger(pair)
    samples: list[Sample] = []

    for row in closed:
        if "manual_trade" in row.get("data_quality_flags", []):
            continue
        outcome = "target" if float(row.get("result_r") or 0.0) > 0 else "stopped"
        samples.append(
            Sample(
                ts=int(row.get("closed_at") or 0),
                pair=row.get("pair") or pair,
                trader_mode=row.get("trader_mode") or "unknown",
                side=row.get("side"),
                source=row.get("zone_type"),
                outcome=outcome,
                fill_label=1,
                target_label=1 if outcome == "target" else 0,
                retire_label=0,
                stop_points=row.get("stop_distance_points"),
                target_points=row.get("target_distance_points"),
                rr=row.get("rr"),
            )
        )

    for row in journal:
        status = (row.get("status") or "").lower()
        if status in {"waiting", "scan_failed"}:
            continue
        quality_flags = row.get("quality_flags") or []
        if "manual_trade" in quality_flags or "invalid_price" in quality_flags:
            continue
        if status == "retired":
            outcome = "retired"
            fill_label = 0
            target_label = 0
            retire_label = 1
        elif status in {"discarded", "blocked_rr", "blocked_structure", "feed_blocked"} or status.startswith("blocked_"):
            outcome = "discarded"
            fill_label = 0
            target_label = 0
            retire_label = 0
        elif status == "expired":
            outcome = "expired"
            fill_label = 0
            target_label = 0
            retire_label = 0
        else:
            continue
        samples.append(
            Sample(
                ts=int(row.get("last_seen_at") or row.get("created_at") or 0),
                pair=row.get("pair") or pair,
                trader_mode=row.get("trader_mode") or "unknown",
                side=row.get("side"),
                source=row.get("source"),
                outcome=outcome,
                fill_label=fill_label,
                target_label=target_label,
                retire_label=retire_label,
            )
        )

    samples.extend(_counterfactual_samples(pair))
    return sorted(samples, key=lambda item: item.ts, reverse=True)


def _counterfactual_samples(pair: str) -> list[Sample]:
    """Turn stored end-of-day what-if replays into training samples. Each
    rejected setup that we replayed against real forward prices becomes a sample
    tagged with a distinct `whatif:<gate>` source — because the feature key is
    `trader_mode|source|side`, these land in their OWN segments and enrich the
    target-probability model without polluting the real taken-trade segments.
    Capped so counterfactuals stay a minority of the training window."""
    out: list[Sample] = []
    try:
        reviews = repo.eod_review_history(pair, limit=30)
    except Exception:  # pragma: no cover - defensive; learning must never crash retrain
        return out
    for review in reviews:
        payload = review.get("review") or {}
        ts = int(review.get("created_at") or payload.get("day_end") or 0)
        for rp in payload.get("replays") or []:
            outcome = rp.get("outcome")
            if outcome in (None, "unavailable"):
                continue
            gate = rp.get("gate") or "unknown"
            out.append(
                Sample(
                    ts=ts,
                    pair=pair,
                    trader_mode=rp.get("trader_mode") or "unknown",
                    side=rp.get("side"),
                    source=f"whatif:{gate}",
                    outcome=outcome,
                    fill_label=1 if rp.get("filled") else 0,
                    target_label=1 if outcome == "target" else 0,
                    retire_label=0,
                    rr=rp.get("counterfactual_r"),
                )
            )
            if len(out) >= COUNTERFACTUAL_CAP:
                return out
    return out


def _fit(samples: list[Sample]) -> dict:
    segments: dict[str, dict[str, float | int]] = {}
    grouped: dict[str, list[Sample]] = {}
    for sample in samples:
        grouped.setdefault(sample.feature_key(), []).append(sample)
    for key, rows in grouped.items():
        segments[key] = _rates(rows)
    return {
        "pair_scope": PAIR_SCOPE,
        "global": _rates(samples),
        "segments": segments,
        "feature_schema_version": 1,
    }


def _baseline(samples: list[Sample]) -> dict:
    return {"global": _rates(samples)}


def _evaluate(artifact: dict, baseline: dict, holdout: list[Sample]) -> dict:
    if not holdout:
        return {
            "holdout_size": 0,
            "candidate_mae": None,
            "baseline_mae": None,
        }
    candidate_errors: list[float] = []
    baseline_errors: list[float] = []
    for sample in holdout:
        actual = float(sample.target_label)
        seg = (artifact.get("segments") or {}).get(sample.feature_key()) or artifact.get("global") or {}
        cand = float(seg.get("target_probability") or 0.0)
        base = float((baseline.get("global") or {}).get("target_probability") or 0.0)
        candidate_errors.append(abs(cand - actual))
        baseline_errors.append(abs(base - actual))
    return {
        "holdout_size": len(holdout),
        "candidate_mae": round(sum(candidate_errors) / len(candidate_errors), 4),
        "baseline_mae": round(sum(baseline_errors) / len(baseline_errors), 4),
    }


def _should_promote(metrics: dict, train_size: int, holdout_size: int) -> bool:
    candidate = metrics.get("candidate_mae")
    baseline = metrics.get("baseline_mae")
    if candidate is None or baseline is None:
        return train_size >= MIN_SAMPLE_SIZE and holdout_size == 0
    return train_size >= MIN_SAMPLE_SIZE and holdout_size >= MIN_HOLDOUT_SIZE and candidate <= baseline


def _promotion_reason(promoted: bool, metrics: dict, sample_count: int) -> str:
    if sample_count < MIN_SAMPLE_SIZE:
        return "Insufficient sample size for promotion."
    candidate = metrics.get("candidate_mae")
    baseline = metrics.get("baseline_mae")
    if candidate is None or baseline is None:
        return "Promoted without holdout because only a minimal starter window is available." if promoted else "Rejected because holdout metrics are unavailable."
    if promoted:
        return f"Candidate MAE {candidate:.4f} beat baseline {baseline:.4f} on holdout."
    return f"Candidate MAE {candidate:.4f} did not beat baseline {baseline:.4f}."


def _dataset_summary(samples: list[Sample]) -> dict:
    counts = Counter(sample.outcome for sample in samples)
    return {
        "outcomes": dict(counts),
        "targets": counts.get("target", 0),
        "stopped": counts.get("stopped", 0),
        "retired": counts.get("retired", 0),
        "discarded": counts.get("discarded", 0),
        "expired": counts.get("expired", 0),
    }


def _rates(samples: list[Sample]) -> dict:
    count = len(samples)
    if count == 0:
        return {
            "count": 0,
            "fill_probability": 0.0,
            "target_probability": 0.0,
            "retire_risk": 0.0,
        }
    fills = sum(sample.fill_label for sample in samples)
    targets = sum(sample.target_label for sample in samples)
    retired = sum(sample.retire_label for sample in samples)
    return {
        "count": count,
        "fill_probability": round(fills / count, 4),
        "target_probability": round(targets / count, 4),
        "retire_risk": round(retired / count, 4),
    }


def _quality_score(fill_probability: float, target_probability: float, retire_risk: float) -> int:
    score = max(0.0, min(1.0, (0.4 * fill_probability) + (0.45 * target_probability) + (0.15 * (1 - retire_risk))))
    return int(round(score * 100))


def _plan_segment_key(plan: dict) -> str:
    return "|".join(
        [
            str(plan.get("trader_mode") or "unknown"),
            str(plan.get("source") or "unknown"),
            str(plan.get("side") or "unknown"),
        ]
    )


def _model_version(ts: int) -> str:
    return f"{MODEL_KIND}-{ts}"


def _disabled_status(pair: str, reason: str) -> dict:
    return {
        "pair": pair,
        "enabled": False,
        "model_kind": MODEL_KIND,
        "active_model_version": None,
        "active_model_status": "disabled",
        "last_retrained": None,
        "training_window": {"start": None, "end": None},
        "sample_count": 0,
        "holdout_size": 0,
        "promotion_result": "disabled",
        "promotion_reason": reason,
        "metrics": {},
        "summary": {},
        "history": [],
    }
