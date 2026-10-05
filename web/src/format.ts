import type { Interval } from "./api/types";

export function formatNumber(value: number): string {
    const abs = Math.abs(value);
    if (abs >= 1_000_000) return `${trim(value / 1_000_000)}M`;
    if (abs >= 10_000) return `${trim(value / 1_000)}k`;
    if (Number.isInteger(value)) return value.toLocaleString("en-US");
    return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function trim(value: number): string {
    return value.toFixed(value >= 100 ? 0 : 1).replace(/\.0$/, "");
}

export function formatPercent(rate: number): string {
    const pct = rate * 100;
    return `${pct >= 10 || pct === 0 ? Math.round(pct) : pct.toFixed(1)}%`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Bucket labels in UTC, the timezone the API reports in. */
export function formatBucket(iso: string, interval: Interval): string {
    const d = new Date(iso);
    const day = `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
    if (interval === "hour") return `${day} ${String(d.getUTCHours()).padStart(2, "0")}:00`;
    if (interval === "month") return `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
    return day;
}

export function seriesLabel(event: string, math: string, breakdown: string | null): string {
    const base = math === "total" ? event : `${event} (${math.replace("_", " ")})`;
    if (breakdown === null) return base;
    const value = breakdown === "$none" ? "(not set)" : breakdown === "$other" ? "Other" : breakdown;
    return `${value}`;
}
