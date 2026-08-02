"""E3 generator population, contract, reproducibility, and leakage tests."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from referencing import Registry, Resource

from packages.contracts import validate_contract_document
from packages.generator import BREAK_FAMILIES, generate_corpus
from packages.generator.core import GeneratorConfig

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "packages" / "contracts" / "schemas"

CONTRACT_BY_KIND = {
    "EXECUTION": "execution-observation",
    "TRADE_CAPTURE": "trade-capture-observation",
    "CONFIRMATION": "confirmation-observation",
    "BOOKING": "booking-observation",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> Registry:
    resources = [
        (path.name, Resource.from_contents(_load_json(path)))
        for path in SCHEMAS.glob("*.schema.json")
    ]
    return Registry().with_resources(resources)


def _assert_json_schema(kind: str, document: dict[str, Any]) -> None:
    contract = CONTRACT_BY_KIND[kind]
    schema = _load_json(SCHEMAS / f"{contract}.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(
        schema,
        registry=_registry(),
        format_checker=FormatChecker(),
    ).validate(document)


def _nested_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(key)
            keys.update(_nested_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_nested_keys(item))
    return keys


def _string_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [item for child in value.values() for item in _string_values(child)]
    if isinstance(value, list):
        return [item for child in value for item in _string_values(child)]
    return [value] if isinstance(value, str) else []


def test_approved_population_and_coverage_are_exact() -> None:
    corpus = generate_corpus()
    manifest = corpus.coverage_manifest

    assert len(corpus.truth_ledger) == 144
    assert manifest["scenario_count"] == 144
    assert manifest["clean_count"] == 48
    assert manifest["mutated_count"] == 96
    assert set(manifest["break_families"]) == set(BREAK_FAMILIES)
    for product in ("FX_SPOT", "FX_FORWARD"):
        product_manifest = manifest["products"][product]
        assert product_manifest["clean"] == 24
        assert product_manifest["mutated"] == 48
        assert product_manifest["total"] == 72
        assert set(product_manifest["by_portfolio"]) == {
            "portfolio_london",
            "portfolio_sydney",
        }
        assert all(count == 6 for count in product_manifest["by_break_family"].values())
    assert all(set(counts.values()) == {6} for counts in manifest["break_families"].values())


def test_every_source_fixture_passes_json_schema_and_pydantic() -> None:
    corpus = generate_corpus()

    for observation in corpus.source_observations:
        kind = observation["observation_kind"]
        _assert_json_schema(kind, observation)
        validate_contract_document(CONTRACT_BY_KIND[kind], observation)


def test_generated_family_set_matches_merged_ts4_taxonomy_contract() -> None:
    taxonomy = _load_json(
        ROOT / "packages" / "contracts" / "examples" / "valid" / "break-taxonomy.json"
    )
    validate_contract_document("break-taxonomy", taxonomy)
    assert {row["family"] for row in taxonomy["families"]} == set(BREAK_FAMILIES)


def test_two_runs_are_byte_identical_in_memory() -> None:
    first = generate_corpus()
    second = generate_corpus()

    assert first.source_observations == second.source_observations
    assert first.truth_ledger == second.truth_ledger
    assert first.coverage_manifest == second.coverage_manifest
    assert first.evidence_manifest == second.evidence_manifest


def test_seed_controls_fixture_values_and_delivery_mutations_are_present() -> None:
    default = generate_corpus()
    alternate = generate_corpus(GeneratorConfig(seed=20260803))
    assert default.source_observations != alternate.source_observations

    behaviours = {
        marker for scenario in default.truth_ledger for marker in scenario["delivery_behaviour"]
    }
    assert {"DUPLICATE", "LATE", "MISSING", "CONFLICTING", "OUT_OF_ORDER"}.issubset(behaviours)


def test_material_times_are_utc_and_ordered() -> None:
    corpus = generate_corpus()
    for observation in corpus.source_observations:
        event_time = observation["event_time"]
        effective_time = observation["effective_time"]
        ingest_time = observation["ingest_time"]
        assert event_time.endswith("Z")
        assert effective_time.endswith("Z")
        assert ingest_time.endswith("Z")
        assert event_time < effective_time < ingest_time


def test_truth_ledger_is_evaluator_only_and_runtime_has_no_truth_metadata() -> None:
    corpus = generate_corpus()
    forbidden_truth_keys = {"action", "approval", "priority"}
    runtime_keys = _nested_keys(corpus.runtime_bundle())

    assert not _nested_keys(corpus.evaluator_bundle()) & forbidden_truth_keys
    assert not runtime_keys & {
        "break_family",
        "variant_id",
        "cause_type",
        "seed",
        "expected_difference_facts",
        "truth_access_classification",
    }
    assert all(
        scenario["truth_access_classification"] == "EVALUATOR_ONLY"
        for scenario in corpus.truth_ledger
    )
    assert all(
        len(scenario["expected_difference_facts"]) == 0
        if scenario["population"] == "CLEAN"
        else len(scenario["expected_difference_facts"]) == 1
        for scenario in corpus.truth_ledger
    )


def test_runtime_values_are_opaque_and_population_order_is_not_a_label() -> None:
    corpus = generate_corpus()
    runtime_values = _string_values(corpus.runtime_bundle())

    marker_pattern = re.compile(r"orphan|candidate|replay|reconciled|scenario_", re.IGNORECASE)
    assert [value for value in runtime_values if marker_pattern.search(value)] == []

    populations = [scenario["population"] for scenario in corpus.truth_ledger]
    assert any(population == "MUTATED" for population in populations[:48])
    assert any(population == "CLEAN" for population in populations[48:])


def test_mutated_truth_graph_preserves_the_complete_cause_chain() -> None:
    corpus = generate_corpus()

    for scenario in corpus.truth_ledger:
        if scenario["population"] != "MUTATED":
            continue
        graph = scenario["provenance_graph"]
        node_types = {node["node_type"] for node in graph["nodes"]}
        relationships = {edge["relationship"] for edge in graph["edges"]}
        assert {
            "SYNTHETIC_CAUSE",
            "SOURCE_MUTATION",
            "DELIVERY_BEHAVIOUR",
            "SOURCE_OBSERVATION",
            "DIFFERENCE_FACT",
            "BREAK_FAMILY",
        } <= node_types
        assert {
            "CAUSE_OF",
            "DELIVERED_AS",
            "CLASSIFIES_AS",
        } <= relationships
        assert {"DELIVERS_OBSERVATION", "DELIVERS_ABSENCE"} & relationships
        assert {"MUTATES_OBSERVATION", "EXPECTS_ABSENCE"} & relationships
        fact_nodes = [node for node in graph["nodes"] if node["node_type"] == "DIFFERENCE_FACT"]
        assert [node["fact"] for node in fact_nodes] == scenario["expected_difference_facts"]
        assert any(
            edge["relationship"] in {"SUPPORTS_DIFFERENCE_FACT", "MATERIALIZES_FACT"}
            for edge in graph["edges"]
        )


def test_mutations_have_only_approved_families_and_truth_causes() -> None:
    corpus = generate_corpus()
    approved_causes = {
        "SYNTHETIC_OPERATOR_ENTRY",
        "SYNTHETIC_MAPPING_TRANSFORMATION",
        "STALE_SOURCE_VERSION",
        "DUPLICATE_OR_REPLAY",
        "LATE_OR_REVISED_SOURCE",
        "UNKNOWN",
    }

    for scenario in corpus.truth_ledger:
        if scenario["population"] == "MUTATED":
            assert scenario["break_family"] in BREAK_FAMILIES
            assert scenario["cause_type"] in approved_causes
            assert scenario["source_mutation"]["field_path"]
            assert scenario["expected_difference_facts"][0]["path"]
            assert any(
                marker in scenario["delivery_behaviour"]
                for marker in ("CONFLICTING", "UNMATCHED", "DUPLICATE", "MISSING")
            )


def test_write_to_emits_deterministic_machine_readable_files(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    generate_corpus().write_to(first_dir)
    generate_corpus().write_to(second_dir)

    first_files = sorted(path.name for path in first_dir.iterdir())
    second_files = sorted(path.name for path in second_dir.iterdir())
    assert first_files == [
        "coverage-manifest.json",
        "evidence-manifest.json",
        "scenario-truth-ledger.json",
        "source-fixtures.json",
    ]
    assert first_files == second_files
    for filename in first_files:
        assert (first_dir / filename).read_bytes() == (second_dir / filename).read_bytes()


def test_invalid_population_configuration_fails_closed() -> None:
    with pytest.raises(ValueError, match="24 clean"):
        GeneratorConfig(clean_per_product=23)


def test_non_utc_offset_start_time_fails_closed() -> None:
    with pytest.raises(ValueError, match="UTC"):
        GeneratorConfig(
            start_time=datetime(
                2026,
                7,
                1,
                10,
                tzinfo=timezone(timedelta(hours=10)),
            )
        )


def test_invalid_observation_is_rejected_by_merged_contract() -> None:
    observation = generate_corpus().source_observations[0]
    invalid = json.loads(json.dumps(observation))
    invalid["event_time"] = "2030-01-01T00:00:00Z"

    with pytest.raises(ValidationError):
        validate_contract_document("execution-observation", invalid)
