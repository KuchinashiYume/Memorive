from __future__ import annotations

from page_sources import settings_scripts


def test_orphan_credential_reference_is_reused_instead_of_created_twice() -> None:
    bridge = settings_scripts()
    assert "orphanReference" in bridge
    assert "credentialRefToWrite" in bridge
    assert "settings.credential_replace" in bridge
    orphan_block = bridge.split("const orphanReference", 1)[1].split(
        "let createdReference", 1
    )[0]
    assert "credential_references.find" in orphan_block
    assert "model_services.some" in orphan_block


def test_network_egress_location_appends_a_country_flag_when_iso_code_is_known() -> None:
    bridge = settings_scripts()
    assert "countryFlag" in bridge
    network = bridge.split("const networkTest", 1)[1].split(
        "const previewAppearance", 1
    )[0]
    assert "receipt.country_code" in network
    assert "countryFlagElement(receipt.country_code)" in network


