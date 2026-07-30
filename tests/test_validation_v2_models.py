from __future__ import annotations

from dataclasses import replace

import pytest

from market_structure_lab.research.validation_v2_models import (
    AccessAuditLedgerIdentityV2,
    AggregatePublicationIdentityV2,
    CostAuthorityIdentityV2,
    DevelopmentSplitIdentityV2,
    PrecisionAuthorityIdentityV2,
    SourceCoverageIdentityV2,
    SourcePublicationIdentityV2,
    ValidationProgrammeConfigV2,
    ValidationRosterIdentityV2,
    load_validation_programme_config_v2,
)


def _identity(identity_type: type, marker: str):
    return identity_type.from_payload({"marker": marker})


def _config(**changes: object) -> ValidationProgrammeConfigV2:
    values: dict[str, object] = {
        "implementation_checkpoint": "1" * 40,
        "coverage_identity": _identity(SourceCoverageIdentityV2, "coverage"),
        "split_identity": _identity(DevelopmentSplitIdentityV2, "split"),
        "source_identity": _identity(SourcePublicationIdentityV2, "source"),
        "aggregate_identity": _identity(AggregatePublicationIdentityV2, "aggregate"),
        "precision_identity": _identity(PrecisionAuthorityIdentityV2, "precision"),
        "cost_identity": _identity(CostAuthorityIdentityV2, "cost"),
        "roster_identity": _identity(ValidationRosterIdentityV2, "roster"),
        "access_ledger_identity": _identity(AccessAuditLedgerIdentityV2, "ledger"),
        "policy_identities": (("controls", "2" * 64), ("statistics", "3" * 64)),
        "work_budget_sha256": "4" * 64,
        "programme_metadata": (("operator", "development"),),
    }
    values.update(changes)
    return ValidationProgrammeConfigV2(**values)  # type: ignore[arg-type]


def test_split_identity_changes_config_and_programme_identity() -> None:
    first = _config()
    second = replace(
        first,
        split_identity=_identity(DevelopmentSplitIdentityV2, "changed-split"),
    )

    assert first.config_sha256 != second.config_sha256
    assert first.programme_id != second.programme_id


def test_programme_id_is_derived_after_config_canonicalization() -> None:
    config = _config()
    payload = config.to_publication_dict()

    assert "programme_id" not in config.to_config_dict()
    assert payload["programme_id"] == config.programme_id
    assert payload["config_sha256"] == config.config_sha256
    assert load_validation_programme_config_v2(payload) == config

    preimage = dict(config.to_config_dict())
    preimage["programme_id"] = config.programme_id
    with pytest.raises(ValueError, match="programme_id.*preimage"):
        ValidationProgrammeConfigV2.from_config_dict(preimage)


def test_config_schema_has_no_dataset_sha256_alias() -> None:
    payload = _config().to_config_dict()
    payload["dataset_sha256"] = "5" * 64

    with pytest.raises(ValueError, match="unexpected"):
        ValidationProgrammeConfigV2.from_config_dict(payload)


@pytest.mark.parametrize(
    ("field", "v1_value"),
    (
        ("coverage_identity", "cb399ac01d0beeea4666da071d9186e9a144f3f7fe5e4d1bf760696c631e8529"),
        ("split_identity", "51bb36d88508fc848b4fd947a827376c633d86274b6ed8a43aadb5e79520f79b"),
        ("source_identity", "80730e2523be9c2bf8463716d806164889df2693007316e20127bccb3f19e5d7"),
    ),
)
def test_v1_lineage_strings_cannot_substitute_for_v2_publications(
    field: str,
    v1_value: str,
) -> None:
    with pytest.raises(TypeError, match="V2 identity"):
        _config(**{field: v1_value})


def test_publication_identity_types_are_not_interchangeable() -> None:
    with pytest.raises(TypeError, match="V2 identity"):
        _config(source_identity=_identity(AggregatePublicationIdentityV2, "not-source"))


def test_loader_rejects_v1_programme_schema() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        load_validation_programme_config_v2(
            {
                "schema_version": "validation-programme-config-v1",
                "programme_id": "VP-" + "0" * 64,
            }
        )
