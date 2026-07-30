from __future__ import annotations

from market_structure_lab.cli import acquire_binance_archive_v2 as cli


def test_acquisition_cli_has_no_budget_or_scope_overrides() -> None:
    parser = cli.build_parser()
    destinations = {action.dest for action in parser._actions}
    assert destinations >= {
        "boundary_publication",
        "audit_ledger_root",
        "request_manifest",
        "cache_root",
        "output_root",
    }
    assert destinations.isdisjoint(
        {"symbol", "start", "end", "max_requests", "retry_ceiling", "allowed_origin"}
    )
