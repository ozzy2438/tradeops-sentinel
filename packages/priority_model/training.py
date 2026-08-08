"""Reproducible synthetic training and validation for the priority model."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np

from packages.remediation.models import BreakFacts

from .features import BREAK_FAMILIES, FEATURE_NAMES, FEATURE_VERSION, feature_vector

MODEL_VERSION = "priority-lgbm-1.0.0"
TRAINING_SEED = 20260808
SAMPLE_COUNT = 2400
TRAIN_COUNT = 1920
PRIORITY_THRESHOLDS = {"medium": 0.25, "high": 0.50, "critical": 0.75}
THRESHOLD_POLICY = "score:<0.25 LOW;<0.50 MEDIUM;<0.75 HIGH;else CRITICAL"

CONDITION_BY_FAMILY = {
    "MISSING_REQUIRED_SOURCE": "MISSING_SOURCE_AFTER_WATERMARK",
    "AMBIGUOUS_OR_UNMATCHED_LINKAGE": "LINKAGE_CANDIDATE_SCOPE_INVARIANT",
    "DUPLICATE_SOURCE_CONFLICT": "DUPLICATE_SOURCE_IDENTITY_CONTENT",
    "CURRENCY_PAIR_OR_SIDE_MISMATCH": "EXACT_CURRENCY_PAIR_SIDE",
    "ECONOMIC_VALUE_MISMATCH": "DECIMAL_OUTSIDE_TOLERANCE",
    "TRADE_OR_VALUE_DATE_MISMATCH": "EXACT_TRADE_VALUE_DATE",
    "LIFECYCLE_STATUS_MISMATCH": "ALLOWED_LIFECYCLE_RELATION",
    "POST_ACTION_VERIFICATION_FAILURE": "POST_ACTION_READBACK_RECONCILIATION",
}

FIELD_BY_FAMILY = {
    "MISSING_REQUIRED_SOURCE": "/source/required_observation",
    "AMBIGUOUS_OR_UNMATCHED_LINKAGE": "/linkage/trade_id",
    "DUPLICATE_SOURCE_CONFLICT": "/source/content_hash",
    "CURRENCY_PAIR_OR_SIDE_MISMATCH": "/payload/base_currency",
    "ECONOMIC_VALUE_MISMATCH": "/payload/base_amount",
    "TRADE_OR_VALUE_DATE_MISMATCH": "/payload/value_date",
    "LIFECYCLE_STATUS_MISMATCH": "/payload/status",
    "POST_ACTION_VERIFICATION_FAILURE": "/payload/base_amount",
}


def _synthetic_dataset() -> tuple[np.ndarray, np.ndarray]:
    """Generate independent point-in-time cases with no outcome/truth features."""

    rng = np.random.default_rng(TRAINING_SEED)
    rows: list[tuple[float, ...]] = []
    labels: list[int] = []
    currencies = ("USD", "EUR", "JPY", "GBP", "AUD", "CAD", "CHF", "SGD")

    for index in range(SAMPLE_COUNT):
        family = BREAK_FAMILIES[int(rng.integers(0, len(BREAK_FAMILIES)))]
        product_type = "FX_FORWARD" if rng.random() < 0.48 else "FX_SPOT"
        trade_value = float(10 ** rng.uniform(3.7, 8.2))
        gap_bps = float(min(rng.lognormal(mean=1.25, sigma=1.0), 500.0))
        if family not in {"ECONOMIC_VALUE_MISMATCH", "POST_ACTION_VERIFICATION_FAILURE"}:
            gap_bps *= 0.08
        expected = trade_value
        observed = expected * (1.0 + gap_bps / 10_000.0)
        expected_source = "FIX_EXECUTION" if rng.random() < 0.72 else "FPML_CONFIRMATION"
        observed_source = "MOCK_LEGACY_BOOKING" if rng.random() < 0.66 else "FIX_TRADE_CAPTURE"

        facts = BreakFacts(
            break_id=f"synthetic_break_{index:05d}",
            break_family=family,
            condition_code=CONDITION_BY_FAMILY[family],
            product_type=product_type,
            trade_id=f"synthetic_trade_{index:05d}",
            field_path=FIELD_BY_FAMILY[family],
            expected_value=f"{expected:.6f}",
            observed_value=f"{observed:.6f}",
            expected_source_system=expected_source,
            observed_source_system=observed_source,
            trade_value_amount=f"{trade_value:.6f}",
            trade_value_currency=currencies[int(rng.integers(0, len(currencies)))],
        )
        vector = feature_vector(facts)
        feature = dict(zip(FEATURE_NAMES, vector, strict=True))

        # The target is a synthetic escalation outcome, not the deterministic
        # severity field and not evaluator scenario truth.  Noise prevents the
        # model from merely memorising a hand-written rule.
        logit = (
            -5.5
            + 0.95 * (feature["log10_trade_value"] - 4.0)
            + 0.012 * feature["relative_value_gap_bps"]
            + 0.65 * feature["product_is_forward"]
            + 1.80 * feature["family__economic_value_mismatch"]
            + 2.35 * feature["family__post_action_verification_failure"]
            + 1.25 * feature["family__currency_pair_or_side_mismatch"]
            + 0.90 * feature["family__missing_required_source"]
            + 0.55 * feature["observed_source_is_legacy_booking"]
            + 0.35 * feature["expected_source_is_fix_execution"]
            + float(rng.normal(0.0, 0.30))
        )
        probability = 1.0 / (1.0 + math.exp(-logit))
        rows.append(vector)
        labels.append(int(rng.random() < probability))

    return np.asarray(rows, dtype=np.float64), np.asarray(labels, dtype=np.int32)


def _roc_auc(labels: np.ndarray, scores: np.ndarray) -> float:
    positives = scores[labels == 1]
    negatives = scores[labels == 0]
    if not len(positives) or not len(negatives):
        raise RuntimeError("validation split must contain both classes")
    comparisons = positives[:, None] - negatives[None, :]
    return float((np.sum(comparisons > 0) + 0.5 * np.sum(comparisons == 0)) / comparisons.size)


def _metrics(labels: np.ndarray, scores: np.ndarray) -> dict[str, float]:
    predicted = (scores >= 0.5).astype(np.int32)
    return {
        "roc_auc": round(_roc_auc(labels, scores), 8),
        "accuracy_at_0_5": round(float(np.mean(predicted == labels)), 8),
        "brier_score": round(float(np.mean((scores - labels) ** 2)), 8),
        "positive_rate": round(float(np.mean(labels)), 8),
    }


def _data_sha256(features: np.ndarray, labels: np.ndarray) -> str:
    digest = hashlib.sha256()
    digest.update(features.tobytes(order="C"))
    digest.update(labels.tobytes(order="C"))
    return "sha256:" + digest.hexdigest()


def train(output_directory: Path) -> dict[str, Any]:
    output_directory.mkdir(parents=True, exist_ok=True)
    features, labels = _synthetic_dataset()
    train_features, valid_features = features[:TRAIN_COUNT], features[TRAIN_COUNT:]
    train_labels, valid_labels = labels[:TRAIN_COUNT], labels[TRAIN_COUNT:]

    train_set = lgb.Dataset(
        train_features,
        label=train_labels,
        feature_name=list(FEATURE_NAMES),
        free_raw_data=False,
    )
    booster = lgb.train(
        {
            "objective": "binary",
            "metric": "binary_logloss",
            "learning_rate": 0.045,
            "num_leaves": 15,
            "min_data_in_leaf": 24,
            "feature_fraction": 0.90,
            "bagging_fraction": 0.90,
            "bagging_freq": 1,
            "lambda_l1": 0.10,
            "lambda_l2": 0.20,
            "seed": TRAINING_SEED,
            "feature_fraction_seed": TRAINING_SEED,
            "bagging_seed": TRAINING_SEED,
            "data_random_seed": TRAINING_SEED,
            "deterministic": True,
            "force_col_wise": True,
            "num_threads": 1,
            "verbosity": -1,
        },
        train_set,
        num_boost_round=120,
    )
    model_path = output_directory / "priority_model.txt"
    booster.save_model(str(model_path))
    validation_scores = np.asarray(booster.predict(valid_features, num_threads=1))
    model_bytes = model_path.read_bytes()
    metadata: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "feature_version": FEATURE_VERSION,
        "training_data": "SYNTHETIC_ONLY",
        "lightgbm_version": lgb.__version__,
        "training_seed": TRAINING_SEED,
        "sample_count": SAMPLE_COUNT,
        "train_count": TRAIN_COUNT,
        "validation_count": SAMPLE_COUNT - TRAIN_COUNT,
        "feature_names": list(FEATURE_NAMES),
        "priority_thresholds": PRIORITY_THRESHOLDS,
        "threshold_policy": THRESHOLD_POLICY,
        "dataset_sha256": _data_sha256(features, labels),
        "model_sha256": "sha256:" + hashlib.sha256(model_bytes).hexdigest(),
        "validation_metrics": _metrics(valid_labels, validation_scores),
        "limitations": (
            "Synthetic demonstration labels only; metrics validate the engineering pipeline, "
            "not predictive validity on real bank operations."
        ),
    }
    (output_directory / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return metadata


def validate(artifact_directory: Path) -> dict[str, Any]:
    model_path = artifact_directory / "priority_model.txt"
    metadata_path = artifact_directory / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    model_bytes = model_path.read_bytes()
    actual_hash = "sha256:" + hashlib.sha256(model_bytes).hexdigest()
    if metadata["model_sha256"] != actual_hash:
        raise RuntimeError("stored priority model hash does not match metadata")
    if metadata["lightgbm_version"] != lgb.__version__:
        raise RuntimeError("installed LightGBM version does not match training metadata")

    features, labels = _synthetic_dataset()
    if metadata["dataset_sha256"] != _data_sha256(features, labels):
        raise RuntimeError("synthetic validation dataset is not reproducible")
    booster = lgb.Booster(model_file=str(model_path))
    scores = np.asarray(booster.predict(features[TRAIN_COUNT:], num_threads=1))
    metrics = _metrics(labels[TRAIN_COUNT:], scores)
    if metrics != metadata["validation_metrics"]:
        raise RuntimeError(
            f"validation metrics changed: expected {metadata['validation_metrics']}, got {metrics}"
        )
    return {"status": "ok", "model_sha256": actual_hash, "validation_metrics": metrics}


__all__ = ["train", "validate"]
