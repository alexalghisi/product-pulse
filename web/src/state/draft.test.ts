import type { EventSummary } from "../api/types";
import { defaultQuery, querySummary } from "./draft";

const events = (...names: string[]): EventSummary[] =>
    names.map((name, i) => ({ name, count: 100 - i, last_seen: "2026-06-30T00:00:00Z" }));

it("prefers the activation funnel when the project has it", () => {
    const q = defaultQuery("funnel", events("$pageview", "project_created", "signed_up", "insight_created"));
    expect(q.kind === "funnel" && q.steps.map((s) => s.event)).toEqual([
        "signed_up",
        "project_created",
        "insight_created",
    ]);
});

it("falls back to the most common events", () => {
    const q = defaultQuery("funnel", events("open", "click"));
    expect(querySummary(q)).toBe("open → click");
    expect(querySummary(defaultQuery("trend", events("open")))).toBe("open");
});

it("builds retention from sign-up to usage", () => {
    const q = defaultQuery("retention", events("$pageview", "signed_up", "insight_viewed"));
    expect(querySummary(q)).toBe("signed_up → insight_viewed (weekly)");
});
