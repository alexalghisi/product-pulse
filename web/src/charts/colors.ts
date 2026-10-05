export const SERIES_SLOTS = 8;

/** Categorical colour by slot, in the palette's fixed order; "Other" is always neutral. */
export function seriesColor(slot: number, isOther = false): string {
    if (isOther || slot >= SERIES_SLOTS) return "var(--series-other)";
    return `var(--series-${slot + 1})`;
}

/** Sequential step (0..6) for a rate in [0, 1]. */
export function sequentialStep(rate: number): number {
    if (!(rate > 0)) return 0;
    return Math.min(6, 1 + Math.floor(rate * 6));
}
