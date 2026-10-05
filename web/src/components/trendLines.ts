import type { TrendResult } from "../api/types";
import type { Line } from "../charts/LineChart";
import { seriesColor } from "../charts/colors";
import { seriesLabel } from "../format";

/** One colour per series in result order; "Other" and "(not set)" stay neutral-last. */
export function colorTrendLines(result: TrendResult): Line[] {
    return result.series.map((s, i) => ({
        key: `${s.event}:${s.math}:${s.breakdown_value ?? ""}:${i}`,
        label: seriesLabel(s.event, s.math, s.breakdown_value),
        color: seriesColor(i, s.breakdown_value === "$other"),
        data: s.data,
    }));
}

/** Whether the last bucket is still filling up (the range ends inside it, e.g. today). */
export function lastBucketIsPartial(result: TrendResult): boolean {
    const last = result.buckets[result.buckets.length - 1];
    if (!last) return false;
    const start = new Date(last);
    const next = new Date(start);
    if (result.interval === "hour") next.setUTCHours(start.getUTCHours() + 1);
    else if (result.interval === "day") next.setUTCDate(start.getUTCDate() + 1);
    else if (result.interval === "week") next.setUTCDate(start.getUTCDate() + 7);
    else next.setUTCMonth(start.getUTCMonth() + 1);
    return new Date(result.date_to) < next;
}
