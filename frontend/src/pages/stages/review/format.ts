// Shared constants + helpers for the review cube panes. Kept separate from
// the component files so React Fast Refresh can hot-update components
// cleanly (it bails on files that mix component + non-component exports).

export const CELL_WIDTH = 110;
export const ID_COL_WIDTH = 130;
export const ROW_H = 36;

export function formatCell(v: number): string {
  if (v === 0) return "0";
  return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
}
