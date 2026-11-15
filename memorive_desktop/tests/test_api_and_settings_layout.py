from __future__ import annotations

from pathlib import Path


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ATTEMPT_ROOT / "product" / "web"


def _read(name: str) -> str:
    return (WEB_ROOT / name).read_text(encoding="utf-8")


def test_orphan_credential_reference_is_reused_instead_of_created_twice() -> None:
    bridge = _read("settings_bridge.js")
    assert "orphanReference" in bridge
    assert "credentialRefToWrite" in bridge
    assert "settings.credential_replace" in bridge
    orphan_block = bridge.split("const orphanReference", 1)[1].split(
        "let createdReference", 1
    )[0]
    assert "credential_references.find" in orphan_block
    assert "model_services.some" in orphan_block


def test_network_egress_location_appends_a_country_flag_when_iso_code_is_known() -> None:
    bridge = _read("settings_bridge.js")
    assert "countryFlag" in bridge
    network = bridge.split("const networkTest", 1)[1].split(
        "const previewAppearance", 1
    )[0]
    assert "receipt.country_code" in network
    assert "countryFlagElement(receipt.country_code)" in network


