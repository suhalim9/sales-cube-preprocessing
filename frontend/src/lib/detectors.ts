import type { AnomalyType, SuggestedFix } from "@/api/types";

export interface DetectorMeta {
  label: string;
  short: string;
  color: string;
  bg: string;
}

export const DETECTOR_META: Record<AnomalyType, DetectorMeta> = {
  negative: {
    label: "Negative values",
    short: "Cells with value < 0",
    color: "oklch(0.65 0.21 27)",
    bg: "oklch(0.65 0.21 27 / 0.12)",
  },
  refund: {
    label: "Refunds",
    short: "Negative patterns suggesting reversals",
    color: "oklch(0.75 0.18 60)",
    bg: "oklch(0.75 0.18 60 / 0.12)",
  },
  double_booking: {
    label: "Double-bookings",
    short: "Spike followed by zero (probable duplicate)",
    color: "oklch(0.65 0.18 250)",
    bg: "oklch(0.65 0.18 250 / 0.12)",
  },
  outlier: {
    label: "Outliers",
    short: "Cells outside Q1 ± 1.5·IQR per row",
    color: "oklch(0.65 0.22 310)",
    bg: "oklch(0.65 0.22 310 / 0.12)",
  },
};

// Each detector contributes one or more actions to the change log. The first
// entry is the detector's default; additional entries are alternatives. Every
// detector offers "Keep as is" so the analyst can always record a "reviewed,
// no change" decision in the audit log regardless of detector.
export const DETECTOR_ACTIONS: Record<
  AnomalyType,
  Array<{ fix: SuggestedFix; label: string }>
> = {
  negative: [
    { fix: "set_to_zero", label: "Set to 0" },
    { fix: "keep_as_is", label: "Keep as is" },
  ],
  refund: [
    { fix: "set_to_zero", label: "Apply refund" },
    { fix: "keep_as_is", label: "Keep as is" },
  ],
  double_booking: [
    { fix: "split_evenly", label: "Split evenly" },
    { fix: "keep_as_is", label: "Keep as is" },
  ],
  outlier: [
    { fix: "keep_as_is", label: "Keep as is" },
    { fix: "set_to_zero", label: "Set to 0" },
  ],
};

// Default action for a detector — used when staging from the cube
// directly (without picking an explicit alternative in the change log).
export const DETECTOR_ACTION: Record<
  AnomalyType,
  { fix: SuggestedFix; label: string }
> = {
  negative: DETECTOR_ACTIONS.negative[0],
  refund: DETECTOR_ACTIONS.refund[0],
  double_booking: DETECTOR_ACTIONS.double_booking[0],
  outlier: DETECTOR_ACTIONS.outlier[0],
};
