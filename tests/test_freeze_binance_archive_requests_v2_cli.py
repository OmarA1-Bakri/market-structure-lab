from __future__ import annotations

from market_structure_lab.cli import freeze_binance_archive_requests_v2 as cli


def test_freezer_cli_exposes_offline_policy_without_network_switch() -> None:
    parser = cli.build_parser()
    destinations = {action.dest for action in parser._actions}
    assert "allowed_origin" in destinations
    assert "require_ca_validation" in destinations
    assert "request_manifest" not in destinations
    assert "network" not in destinations
