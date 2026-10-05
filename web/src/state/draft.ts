import type { EventSummary, InsightKind, InsightQuery } from "../api/types";

const FUNNEL_PREFERENCE = ["signed_up", "project_created", "insight_created", "invited_teammate"];

function pick(events: EventSummary[], preferred: string[], fallbackIndex: number): string {
    const names = events.map((e) => e.name);
    return preferred.find((p) => names.includes(p)) ?? names[fallbackIndex] ?? names[0] ?? "$pageview";
}

/** A reasonable first query of each kind for whatever events the project has. */
export function defaultQuery(kind: InsightKind, events: EventSummary[]): InsightQuery {
    const names = events.map((e) => e.name);
    switch (kind) {
        case "trend":
            return {
                kind,
                series: [{ event: pick(events, ["$pageview"], 0), math: "total", filters: [] }],
                date_range: { date_from: "-30d" },
                interval: "day",
                breakdown: null,
                filters: [],
            };
        case "funnel": {
            const preferred = FUNNEL_PREFERENCE.filter((e) => names.includes(e)).slice(0, 3);
            const steps = preferred.length >= 2 ? preferred : names.slice(0, 2);
            return {
                kind,
                steps: steps.map((event) => ({ event, filters: [] })),
                conversion_window: 14,
                conversion_window_unit: "day",
                date_range: { date_from: "-30d" },
                breakdown: null,
                filters: [],
            };
        }
        case "retention":
            return {
                kind,
                start_event: { event: pick(events, ["signed_up"], 0), filters: [] },
                return_event: { event: pick(events, ["insight_viewed", "$pageview"], 0), filters: [] },
                period: "week",
                periods: 8,
                date_range: { date_from: "-8w" },
                filters: [],
            };
    }
}

export function querySummary(query: InsightQuery): string {
    switch (query.kind) {
        case "trend":
            return query.series.map((s) => s.event).join(", ") + (query.breakdown ? ` by ${query.breakdown}` : "");
        case "funnel":
            return query.steps.map((s) => s.event).join(" → ");
        case "retention":
            return `${query.start_event.event} → ${query.return_event.event} (${query.period}ly)`;
    }
}
