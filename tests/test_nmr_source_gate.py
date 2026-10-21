"""Offline dual-source NMR gate regressions."""

import pytest
from online_fitter.source_gate import evaluate_source


def judge(**changes):
    inputs = dict(
        selected_source="labview",
        transport_source="labview",
        payload_source=None,
        nmr_mask=0,
        nmr_comm=1,
        route_age_s=0.2,
        data_age_s=1.0,
        stream_connected=True,
    )
    inputs.update(changes)
    return evaluate_source(**inputs)


def test_labview_legacy_packet():
    assert judge().accepted


def test_pynmr_packet():
    result = judge(
        selected_source="pynmr",
        transport_source="pynmr",
        payload_source="pynmr",
        nmr_mask=0,
    )
    assert result.accepted


@pytest.mark.parametrize("mask", [1, 2, 3, 7, None, True])
def test_invalid_route(mask):
    assert not judge(nmr_mask=mask).accepted


@pytest.mark.parametrize("source", ["labview", "pynmr"])
def test_both_sources_use_connected_nmr_path(source):
    assert judge(
        selected_source=source,
        transport_source=source,
        payload_source=source,
        nmr_mask=0,
    ).accepted


@pytest.mark.parametrize("age", [-1, 6, None, float("nan")])
def test_invalid_switch_age(age):
    assert not judge(route_age_s=age).accepted


@pytest.mark.parametrize("age", [-1, 31, None, float("inf")])
def test_invalid_measurement_age(age):
    assert not judge(data_age_s=age).accepted


def test_no_pynmr_fallback():
    assert not judge(
        selected_source="pynmr",
        transport_source="labview",
        nmr_mask=0,
    ).accepted


def test_disconnected_stream():
    assert not judge(stream_connected=False).accepted


def test_disconnected_switch():
    assert not judge(nmr_comm=0).accepted


def test_pynmr_requires_tag():
    assert not judge(
        selected_source="pynmr",
        transport_source="pynmr",
        nmr_mask=0,
    ).accepted


def test_spoofed_identity():
    assert not judge(payload_source="pynmr").accepted


def test_invalid_payload_identity():
    assert not judge(payload_source=123).accepted


@pytest.mark.parametrize("selected", [None, "", "unknown"])
def test_invalid_selection(selected):
    assert not judge(selected_source=selected).accepted


def test_both_sources_require_fresh_data():
    for selected, mask in (("labview", 0), ("pynmr", 0)):
        assert not judge(
            selected_source=selected,
            transport_source=selected,
            payload_source=selected,
            nmr_mask=mask,
            data_age_s=40,
        ).accepted
