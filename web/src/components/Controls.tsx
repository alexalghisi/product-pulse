import type { Api } from "../api/client";
import type { EventSummary, FunnelQuery, InsightQuery, Math as MathKind, RetentionQuery, TrendQuery } from "../api/types";
import { BreakdownSelect, EventSelect, RangeSelect, usePropertyKeys } from "./Selects";

interface Props<Q> {
    api: Api;
    projectId: number;
    events: EventSummary[];
    query: Q;
    onChange: (query: InsightQuery) => void;
}

const MATHS: { value: MathKind; label: string }[] = [
    { value: "total", label: "Total events" },
    { value: "unique_persons", label: "Unique persons" },
];

export function TrendControls({ api, projectId, events, query, onChange }: Props<TrendQuery>) {
    const series = query.series[0] ?? { event: "$pageview", math: "total" as const, filters: [] };
    const keys = usePropertyKeys(api, projectId, series.event);
    return (
        <div className="controls">
            <EventSelect
                label="Event"
                value={series.event}
                events={events}
                onChange={(event) => onChange({ ...query, series: [{ ...series, event }], breakdown: null })}
            />
            <label className="field">
                Measure
                <select
                    value={series.math}
                    onChange={(e) => onChange({ ...query, series: [{ ...series, math: e.target.value as MathKind }] })}
                >
                    {MATHS.map((m) => (
                        <option key={m.value} value={m.value}>
                            {m.label}
                        </option>
                    ))}
                </select>
            </label>
            <BreakdownSelect value={query.breakdown} keys={keys} onChange={(breakdown) => onChange({ ...query, breakdown })} />
            <RangeSelect
                value={query.date_range.date_from}
                onChange={(date_from) => onChange({ ...query, date_range: { date_from } })}
            />
            <label className="field">
                Interval
                <select
                    value={query.interval}
                    onChange={(e) => onChange({ ...query, interval: e.target.value as TrendQuery["interval"] })}
                >
                    <option value="hour">Hour</option>
                    <option value="day">Day</option>
                    <option value="week">Week</option>
                    <option value="month">Month</option>
                </select>
            </label>
        </div>
    );
}

export function FunnelControls({ api, projectId, events, query, onChange }: Props<FunnelQuery>) {
    const keys = usePropertyKeys(api, projectId, query.steps[0]?.event ?? "");
    const setStep = (index: number, event: string) =>
        onChange({ ...query, steps: query.steps.map((s, i) => (i === index ? { ...s, event } : s)) });
    return (
        <>
            <div className="controls">
                <div className="steps">
                    {query.steps.map((step, i) => (
                        <span key={i} style={{ display: "contents" }}>
                            {i > 0 && <span className="step-arrow">→</span>}
                            <EventSelect label={`Step ${i + 1}`} value={step.event} events={events} onChange={(e) => setStep(i, e)} />
                        </span>
                    ))}
                    {query.steps.length < 10 && (
                        <button
                            className="btn ghost"
                            onClick={() =>
                                onChange({ ...query, steps: [...query.steps, { event: events[0]?.name ?? "$pageview", filters: [] }] })
                            }
                        >
                            + Step
                        </button>
                    )}
                    {query.steps.length > 2 && (
                        <button className="btn ghost" onClick={() => onChange({ ...query, steps: query.steps.slice(0, -1) })}>
                            Remove last
                        </button>
                    )}
                </div>
            </div>
            <div className="controls">
                <label className="field">
                    Conversion window
                    <span style={{ display: "flex", gap: 4 }}>
                        <input
                            type="number"
                            min={1}
                            max={365}
                            value={query.conversion_window}
                            onChange={(e) => onChange({ ...query, conversion_window: Math.max(1, Number(e.target.value) || 1) })}
                        />
                        <select
                            value={query.conversion_window_unit}
                            onChange={(e) =>
                                onChange({ ...query, conversion_window_unit: e.target.value as FunnelQuery["conversion_window_unit"] })
                            }
                        >
                            <option value="minute">minutes</option>
                            <option value="hour">hours</option>
                            <option value="day">days</option>
                            <option value="week">weeks</option>
                        </select>
                    </span>
                </label>
                <BreakdownSelect value={query.breakdown} keys={keys} onChange={(breakdown) => onChange({ ...query, breakdown })} />
                <RangeSelect value={query.date_range.date_from} onChange={(date_from) => onChange({ ...query, date_range: { date_from } })} />
            </div>
        </>
    );
}

export function RetentionControls({ events, query, onChange }: Props<RetentionQuery>) {
    return (
        <div className="controls">
            <EventSelect
                label="Started with"
                value={query.start_event.event}
                events={events}
                onChange={(event) => onChange({ ...query, start_event: { event, filters: [] } })}
            />
            <EventSelect
                label="Came back for"
                value={query.return_event.event}
                events={events}
                onChange={(event) => onChange({ ...query, return_event: { event, filters: [] } })}
            />
            <label className="field">
                Period
                <select value={query.period} onChange={(e) => onChange({ ...query, period: e.target.value as RetentionQuery["period"] })}>
                    <option value="day">Day</option>
                    <option value="week">Week</option>
                </select>
            </label>
            <RangeSelect value={query.date_range.date_from} onChange={(date_from) => onChange({ ...query, date_range: { date_from } })} />
        </div>
    );
}
