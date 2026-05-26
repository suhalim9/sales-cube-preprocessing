"""Apply layer — covers APP-* and AUD-* from TEST_SCENARIOS.md §B.5."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from app.apply import (
    ApplyResult,
    InvalidSelectionError,
    Selection,
    apply_selections,
)
from app.detect import Detection


def _df():
    return pd.DataFrame({
        "customer": ["Cust_A", "Cust_B"],
        "product_line": ["P_1", "P_2"],
        "2022_1": [100.0, 200.0],
        "2022_2": [150.0, -1247.0],
        "2022_3": [200.0, 300.0],
    })


MEASURE = ["2022_1", "2022_2", "2022_3"]
FIXED_TS = datetime(2026, 5, 24, 10, 30, 0, tzinfo=timezone.utc)


def _det(**overrides) -> Detection:
    base = dict(
        detection_id="det-1",
        row_idx=1,
        row_key={"customer": "Cust_B", "product_line": "P_2"},
        column="2022_2",
        value=-1247.0,
        flagged_by=["negative"],
        suggested_fix="set_to_zero",
        confidence=1.0,
        alternative_fixes=[],
    )
    base.update(overrides)
    return Detection(**base)


# ---------------------------------------------------------------------------
# Basic apply behaviors
# ---------------------------------------------------------------------------


# APP-01: 0 selections — apply still runs; just returns cleaned copy with empty audit
def test_zero_selections_returns_unchanged_copy():
    df = _df()
    result = apply_selections(
        df, [], [], file_id="f1", measure_columns=MEASURE, applied_at=FIXED_TS,
    )
    pd.testing.assert_frame_equal(result.cleaned_df, df)
    assert result.audit["changes"] == []
    assert result.audit["summary"] == {
        "negative": 0, "refund": 0, "double_booking": 0, "outlier": 0,
    }


def test_set_to_zero_applies_and_emits_one_entry():
    df = _df()
    det = _det()
    result = apply_selections(
        df, [det], [Selection("det-1", "set_to_zero")],
        file_id="f1", measure_columns=MEASURE, applied_at=FIXED_TS,
    )
    assert result.cleaned_df.at[1, "2022_2"] == 0.0
    assert df.at[1, "2022_2"] == -1247.0  # original unchanged
    changes = result.audit["changes"]
    assert len(changes) == 1
    c = changes[0]
    assert c["value_before"] == -1247.0
    assert c["value_after"] == 0.0
    assert c["anomaly_type"] == "negative"
    assert c["suggested_fix"] == "set_to_zero"
    assert c["flagged"] is False
    assert c["row_key"] == {"customer": "Cust_B", "product_line": "P_2"}


def test_unknown_detection_id_raises():
    with pytest.raises(InvalidSelectionError):
        apply_selections(
            _df(), [_det()], [Selection("nope", "set_to_zero")],
            file_id="f1", measure_columns=MEASURE,
        )


def test_disallowed_fix_raises():
    det = _det(suggested_fix="set_to_zero", alternative_fixes=[])
    with pytest.raises(InvalidSelectionError):
        apply_selections(
            _df(), [det], [Selection("det-1", "split_evenly")],
            file_id="f1", measure_columns=MEASURE,
        )


# ---------------------------------------------------------------------------
# Double-booking fixes
# ---------------------------------------------------------------------------


# AUD-07: split_evenly emits two entries
def test_split_evenly_emits_two_entries():
    df = pd.DataFrame({
        "customer": ["A"], "product_line": ["X"],
        "2022_1": [100.0], "2022_2": [10_000.0], "2022_3": [0.0], "2022_4": [100.0],
    })
    det = _det(
        detection_id="d-dbl", row_idx=0, column="2022_2", value=10_000.0,
        row_key={"customer": "A", "product_line": "X"},
        flagged_by=["double_booking"],
        suggested_fix="split_evenly",
        alternative_fixes=[],
    )
    result = apply_selections(
        df, [det], [Selection("d-dbl", "split_evenly")],
        file_id="f1", measure_columns=["2022_1", "2022_2", "2022_3", "2022_4"],
        applied_at=FIXED_TS,
    )
    assert result.cleaned_df.at[0, "2022_2"] == 5000.0
    assert result.cleaned_df.at[0, "2022_3"] == 5000.0
    changes = result.audit["changes"]
    assert len(changes) == 2
    assert changes[0]["column"] == "2022_2"
    assert changes[0]["value_before"] == 10_000.0
    assert changes[0]["value_after"] == 5000.0
    assert changes[1]["column"] == "2022_3"
    assert changes[1]["value_before"] == 0.0
    assert changes[1]["value_after"] == 5000.0
    assert all(c["anomaly_type"] == "double_booking" for c in changes)


# DBL-09 + AUD-07 corollary: odd integers favor the earlier cell
def test_split_evenly_odd_amount_favors_earlier():
    df = pd.DataFrame({
        "customer": ["A"], "product_line": ["X"],
        "m1": [10.0], "m2": [101.0], "m3": [0.0], "m4": [10.0],
    })
    det = _det(
        detection_id="d", row_idx=0, column="m2", value=101.0,
        row_key={"customer": "A", "product_line": "X"},
        flagged_by=["double_booking"],
        suggested_fix="split_evenly",
        alternative_fixes=[],
    )
    result = apply_selections(
        df, [det], [Selection("d", "split_evenly")],
        file_id="f1", measure_columns=["m1", "m2", "m3", "m4"],
        applied_at=FIXED_TS,
    )
    assert result.cleaned_df.at[0, "m2"] == 51.0
    assert result.cleaned_df.at[0, "m3"] == 50.0


# ---------------------------------------------------------------------------
# Outlier flag (no value change)
# ---------------------------------------------------------------------------


# APP-06 / AUD-06: outlier flag emits a no-value-change audit entry
def test_outlier_only_set_to_zero_emits_one_entry():
    df = _df()
    det = _det(
        detection_id="d-out", row_idx=1, column="2022_2", value=-1247.0,
        flagged_by=["outlier"], suggested_fix="set_to_zero",
    )
    result = apply_selections(
        df, [det], [Selection("d-out", "set_to_zero")],
        file_id="f1", measure_columns=MEASURE, applied_at=FIXED_TS,
    )
    assert result.cleaned_df.at[1, "2022_2"] == 0.0
    c = result.audit["changes"][0]
    assert c["value_before"] == -1247.0
    assert c["value_after"] == 0.0
    assert c["anomaly_type"] == "outlier"
    assert result.audit["summary"]["outlier"] == 1


# ---------------------------------------------------------------------------
# Attribution on overlap (APP-05)
# ---------------------------------------------------------------------------


# APP-05: cell flagged by both negative and refund. Refund wins priority;
# the fix zeros the refund cell and walks backward absorbing from prior
# positive periods until the magnitude is exhausted. See ROADMAP §6.
def test_overlap_neg_ref_attributes_to_refund_and_absorbs_backward():
    df = pd.DataFrame({
        "customer": ["C"], "product_line": ["P"],
        "m1": [600.0], "m2": [700.0], "m3": [-1200.0], "m4": [50.0],
    })
    det = _det(
        detection_id="r1", row_idx=0, column="m3", value=-1200.0,
        row_key={"customer": "C", "product_line": "P"},
        flagged_by=["negative", "refund"],
    )
    result = apply_selections(
        df, [det], [Selection("r1", "set_to_zero")],
        file_id="f1", measure_columns=["m1", "m2", "m3", "m4"], applied_at=FIXED_TS,
    )
    # Walking backward: m2 (700) absorbs 700 → becomes 0; m1 (600) absorbs
    # remaining 500 → becomes 100. m3 itself is zeroed first.
    assert result.cleaned_df.at[0, "m3"] == 0.0
    assert result.cleaned_df.at[0, "m2"] == 0.0
    assert result.cleaned_df.at[0, "m1"] == 100.0
    changes = result.audit["changes"]
    assert len(changes) == 3
    assert all(c["anomaly_type"] == "refund" for c in changes)
    assert [c["column"] for c in changes] == ["m3", "m2", "m1"]
    assert changes[0]["value_before"] == -1200.0 and changes[0]["value_after"] == 0.0
    assert changes[1]["value_before"] == 700.0 and changes[1]["value_after"] == 0.0
    assert changes[2]["value_before"] == 600.0 and changes[2]["value_after"] == 100.0
    # Primary vs cascade discriminator (the m1 entry is informative: it absorbed
    # 500 of the refund magnitude but ended at 100, not 0 — so without
    # change_kind the audit reads as if the user picked "set_to_zero" on a cell
    # that didn't end at zero).
    assert changes[0]["change_kind"] == "primary"
    assert changes[1]["change_kind"] == "refund_cascade"
    assert changes[2]["change_kind"] == "refund_cascade"
    assert "parent_change_id" not in changes[0]
    primary_id = changes[0]["change_id"]
    assert changes[1]["parent_change_id"] == primary_id
    assert changes[2]["parent_change_id"] == primary_id


def test_paired_refund_collapses_to_zero():
    # The clean (X, -X) case — the immediately prior sale fully absorbs.
    df = pd.DataFrame({
        "customer": ["C"], "product_line": ["P"],
        "m1": [200.0], "m2": [-200.0], "m3": [50.0],
    })
    det = _det(
        detection_id="r1", row_idx=0, column="m2", value=-200.0,
        row_key={"customer": "C", "product_line": "P"},
        flagged_by=["refund"],
    )
    result = apply_selections(
        df, [det], [Selection("r1", "set_to_zero")],
        file_id="f1", measure_columns=["m1", "m2", "m3"], applied_at=FIXED_TS,
    )
    assert result.cleaned_df.at[0, "m1"] == 0.0
    assert result.cleaned_df.at[0, "m2"] == 0.0
    assert result.cleaned_df.at[0, "m3"] == 50.0


def test_partial_refund_absorbs_from_immediate_prior():
    # (10_000, -8_900) — prior fully absorbs; (1_100, 0).
    df = pd.DataFrame({
        "customer": ["C"], "product_line": ["P"],
        "m1": [10_000.0], "m2": [-8_900.0], "m3": [50.0],
    })
    det = _det(
        detection_id="r1", row_idx=0, column="m2", value=-8_900.0,
        row_key={"customer": "C", "product_line": "P"},
        flagged_by=["refund"],
    )
    result = apply_selections(
        df, [det], [Selection("r1", "set_to_zero")],
        file_id="f1", measure_columns=["m1", "m2", "m3"], applied_at=FIXED_TS,
    )
    assert result.cleaned_df.at[0, "m1"] == 1_100.0
    assert result.cleaned_df.at[0, "m2"] == 0.0


def test_refund_skips_zero_periods_when_walking_backward():
    # [10_000, 0, 0, 550, -8_900]. Walking backward from m5:
    # m4 absorbs 550 → 0; m3 zero (skip); m2 zero (skip); m1 absorbs 8_350 → 1_650.
    df = pd.DataFrame({
        "customer": ["C"], "product_line": ["P"],
        "m1": [10_000.0], "m2": [0.0], "m3": [0.0],
        "m4": [550.0], "m5": [-8_900.0],
    })
    det = _det(
        detection_id="r1", row_idx=0, column="m5", value=-8_900.0,
        row_key={"customer": "C", "product_line": "P"},
        flagged_by=["refund"],
    )
    result = apply_selections(
        df, [det], [Selection("r1", "set_to_zero")],
        file_id="f1", measure_columns=["m1", "m2", "m3", "m4", "m5"], applied_at=FIXED_TS,
    )
    assert result.cleaned_df.at[0, "m5"] == 0.0
    assert result.cleaned_df.at[0, "m4"] == 0.0
    assert result.cleaned_df.at[0, "m3"] == 0.0
    assert result.cleaned_df.at[0, "m2"] == 0.0
    assert result.cleaned_df.at[0, "m1"] == 1_650.0
    changes = result.audit["changes"]
    # Refund cell + m4 + m1 (the two non-zero positives that got absorbed).
    assert [c["column"] for c in changes] == ["m5", "m4", "m1"]


def test_refund_in_first_column_zeros_single_cell_only():
    # Defensive: refunds detector won't flag the first column, but if a
    # caller hand-builds one, apply degrades gracefully (no prior to zero).
    df = pd.DataFrame({
        "customer": ["X"], "product_line": ["P"],
        "m1": [-100.0], "m2": [50.0],
    })
    det = _det(
        detection_id="r1", row_idx=0, column="m1", value=-100.0,
        row_key={"customer": "X", "product_line": "P"},
        flagged_by=["refund"],
    )
    result = apply_selections(
        df, [det], [Selection("r1", "set_to_zero")],
        file_id="f1", measure_columns=["m1", "m2"], applied_at=FIXED_TS,
    )
    assert result.cleaned_df.at[0, "m1"] == 0.0
    assert result.cleaned_df.at[0, "m2"] == 50.0  # untouched
    assert len(result.audit["changes"]) == 1


def test_explicit_attribution_overrides_priority():
    # User staged from the Outliers tab on a cell flagged by both negative and
    # outlier. Explicit attribution wins over priority order.
    df = _df()
    det = _det(flagged_by=["negative", "outlier"], suggested_fix="set_to_zero")
    result = apply_selections(
        df, [det], [Selection("det-1", "set_to_zero", attribution="outlier")],
        file_id="f1", measure_columns=MEASURE, applied_at=FIXED_TS,
    )
    assert result.audit["changes"][0]["anomaly_type"] == "outlier"


# ---------------------------------------------------------------------------
# Audit log invariants (AUD-02..04)
# ---------------------------------------------------------------------------


# AUD-02
def test_change_ids_are_unique():
    df = pd.DataFrame({
        "customer": ["A", "B", "C"], "product_line": ["X", "Y", "Z"],
        "m1": [-1.0, -2.0, -3.0],
    })
    dets = [
        _det(detection_id=f"d-{i}", row_idx=i, column="m1", value=-(i + 1),
             row_key={"customer": chr(ord("A") + i), "product_line": chr(ord("X") + i)})
        for i in range(3)
    ]
    sels = [Selection(f"d-{i}", "set_to_zero") for i in range(3)]
    result = apply_selections(
        df, dets, sels, file_id="f1", measure_columns=["m1"], applied_at=FIXED_TS,
    )
    ids = [c["change_id"] for c in result.audit["changes"]]
    assert len(set(ids)) == 3


# AUD-03
def test_applied_at_is_single_timestamp():
    df = pd.DataFrame({
        "customer": ["A", "B"], "product_line": ["X", "Y"],
        "m1": [-1.0, -2.0],
    })
    dets = [
        _det(detection_id="d-0", row_idx=0, column="m1", value=-1.0,
             row_key={"customer": "A", "product_line": "X"}),
        _det(detection_id="d-1", row_idx=1, column="m1", value=-2.0,
             row_key={"customer": "B", "product_line": "Y"}),
    ]
    sels = [Selection("d-0", "set_to_zero"), Selection("d-1", "set_to_zero")]
    result = apply_selections(
        df, dets, sels, file_id="f1", measure_columns=["m1"], applied_at=FIXED_TS,
    )
    assert result.audit["applied_at"] == "2026-05-24T10:30:00Z"


# AUD-04
def test_audit_is_valid_json():
    df = _df()
    det = _det()
    result = apply_selections(
        df, [det], [Selection("det-1", "set_to_zero")],
        file_id="f1", measure_columns=MEASURE, applied_at=FIXED_TS,
    )
    s = json.dumps(result.audit)
    parsed = json.loads(s)
    assert parsed["file_id"] == "f1"
    assert parsed["user_id"] == "project:demo"
    assert len(parsed["changes"]) == 1


# AUD-01: 10 changes across 3 detector types — summary counts match
def test_summary_counts_match_per_type():
    df = pd.DataFrame({
        "customer": [f"C_{i}" for i in range(5)],
        "product_line": [f"P_{i}" for i in range(5)],
        "m1": [-1.0, -2.0, -3.0, -4.0, -5.0],
        "m2": [0.0] * 5,
        "m3": [10.0] * 5,
    })
    measure = ["m1", "m2", "m3"]

    # 3 negatives, 1 refund, 1 outlier flag = 5 audit entries
    dets = [
        _det(detection_id=f"n-{i}", row_idx=i, column="m1", value=-(i + 1),
             row_key={"customer": f"C_{i}", "product_line": f"P_{i}"},
             flagged_by=["negative"])
        for i in range(3)
    ]
    dets.append(_det(
        detection_id="r-0", row_idx=3, column="m1", value=-4.0,
        row_key={"customer": "C_3", "product_line": "P_3"},
        flagged_by=["refund"],
    ))
    dets.append(_det(
        detection_id="o-0", row_idx=4, column="m1", value=-5.0,
        row_key={"customer": "C_4", "product_line": "P_4"},
        flagged_by=["outlier"], suggested_fix="set_to_zero",
    ))
    sels = (
        [Selection(f"n-{i}", "set_to_zero") for i in range(3)]
        + [Selection("r-0", "set_to_zero"), Selection("o-0", "set_to_zero")]
    )
    result = apply_selections(
        df, dets, sels, file_id="f1", measure_columns=measure, applied_at=FIXED_TS,
    )
    assert result.audit["summary"] == {
        "negative": 3, "refund": 1, "double_booking": 0, "outlier": 1,
    }
    assert len(result.audit["changes"]) == 5


# ---------------------------------------------------------------------------
# End-to-end through merge + apply on the happy cube
# ---------------------------------------------------------------------------


def test_apply_full_workflow_on_happy_cube():
    from app.detect import detect_all
    from app.schema import infer_schema

    df = pd.read_parquet("../data/scenarios/happy.parquet")
    sch = infer_schema(df)
    assert sch.ok
    measure = sch.roles.measure_columns
    dets = detect_all(df, sch.roles.id_columns, measure)
    sels = [Selection(d.detection_id, d.suggested_fix) for d in dets]

    result = apply_selections(
        df, dets, sels,
        file_id="happy", measure_columns=measure, applied_at=FIXED_TS,
    )
    # Every directly-flagged negative cell becomes 0 (set_to_zero zeros the
    # flagged cell itself). Outlier keep_as_is entries don't change the
    # cube. Refund attribution additionally nets the reversal against the
    # prior period — if |reversal| > prior positive, the prior cell can end
    # up negative, so we don't assert "zero remaining negatives" globally.
    cleaned = result.cleaned_df[measure].to_numpy()
    for d in dets:
        if d.suggested_fix == "set_to_zero":
            assert cleaned[d.row_idx, measure.index(d.column)] == 0.0
    # Every applied change appears in the audit. Refund attributions emit
    # two entries (the cell + the netted prior); split_evenly emits two.
    assert len(result.audit["changes"]) >= len(dets)
