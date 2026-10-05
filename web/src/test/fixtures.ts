import type { FunnelResult, RetentionResult, TrendResult } from "../api/types";

export const trend: TrendResult = {
    kind: "trend",
    interval: "day",
    date_from: "2026-06-01T00:00:00Z",
    date_to: "2026-06-04T00:00:00Z",
    buckets: ["2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z", "2026-06-03T00:00:00Z"],
    series: [
        { event: "$pageview", math: "total", breakdown_value: "Chrome", data: [3, 0, 0], total: 3 },
        { event: "$pageview", math: "total", breakdown_value: "$other", data: [1, 2, 1], total: 4 },
    ],
};

const step = (event: string, persons: number, start: number, prev: number) => ({
    event,
    persons,
    conversion_from_start: start,
    conversion_from_previous: prev,
});

export const funnel: FunnelResult = {
    kind: "funnel",
    date_from: "2026-06-01T00:00:00Z",
    date_to: "2026-07-01T00:00:00Z",
    conversion_window: "1 day",
    overall: [step("signed_up", 3, 1, 1), step("project_created", 2, 0.6667, 0.6667), step("invited_teammate", 1, 0.3333, 0.5)],
    breakdowns: [],
};

export const funnelByPlan: FunnelResult = {
    ...funnel,
    breakdowns: [
        { breakdown_value: "free", steps: [step("signed_up", 2, 1, 1), step("project_created", 1, 0.5, 0.5), step("invited_teammate", 0, 0, 0)] },
        { breakdown_value: "pro", steps: [step("signed_up", 1, 1, 1), step("project_created", 1, 1, 1), step("invited_teammate", 1, 1, 1)] },
    ],
};

export const retention: RetentionResult = {
    kind: "retention",
    period: "day",
    cohorts: [
        { cohort: "2026-06-01", size: 2, retained: [1, 2, 0], rates: [0.5, 1, 0] },
        { cohort: "2026-06-02", size: 1, retained: [0, 1, null], rates: [0, 1, null] },
    ],
};
