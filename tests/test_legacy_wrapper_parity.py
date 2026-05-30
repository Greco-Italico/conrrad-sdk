"""L2 wrapper parity — legacy imports must match conrrad_sdk exports."""
import warnings

import pytest


def test_kap_escrow_escrow_engine_is_canonical():
    import conrrad_sdk
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        import kap_escrow
    assert kap_escrow.EscrowEngine is conrrad_sdk.EscrowEngine
    assert kap_escrow.TransactionWAL is conrrad_sdk.TransactionWAL


def test_kernell_sdk_agent_is_canonical():
    import conrrad_sdk
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        import kernell_sdk
    assert kernell_sdk.Agent is conrrad_sdk.Agent
    assert kernell_sdk.LLMRouter is conrrad_sdk.LLMRouter


def test_conrrad_sdk_import_no_deprecation():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import conrrad_sdk  # noqa: F401
    dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert not dep, f"conrrad_sdk should not warn: {dep}"
