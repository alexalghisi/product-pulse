import { useEffect, useState } from "react";
import { ApiError, type Api } from "../api/client";
import type { EventSummary, InsightKind, InsightQuery, QueryResult } from "../api/types";
import { defaultQuery, querySummary } from "../state/draft";
import { FunnelControls, RetentionControls, TrendControls } from "./Controls";
import { Result } from "./Result";

const KINDS: { kind: InsightKind; label: string }[] = [
    { kind: "trend", label: "Trends" },
    { kind: "funnel", label: "Funnel" },
    { kind: "retention", label: "Retention" },
];

export interface Props {
    api: Api;
    projectId: number;
    events: EventSummary[];
    query: InsightQuery;
    onQueryChange: (query: InsightQuery) => void;
    onSave: (name: string, query: InsightQuery) => Promise<void>;
    title?: string;
}

export function InsightEditor({ api, projectId, events, query, onQueryChange, onSave, title }: Props) {
    const [result, setResult] = useState<QueryResult | null>(null);
    const [error, setError] = useState<ApiError | null>(null);
    const [loading, setLoading] = useState(false);
    const [elapsed, setElapsed] = useState<number | null>(null);
    const [saving, setSaving] = useState<string | null>(null);

    useEffect(() => {
        // Every edit re-runs the query; an older response must never replace a newer one.
        const controller = new AbortController();
        const started = performance.now();
        setLoading(true);
        const timer = setTimeout(() => {
            api.query(projectId, query, controller.signal)
                .then((r) => {
                    setResult(r);
                    setError(null);
                    setElapsed(performance.now() - started);
                })
                .catch((e: unknown) => {
                    if (controller.signal.aborted) return;
                    setError(e instanceof ApiError ? e : new ApiError(0, { message: String(e) }));
                })
                .finally(() => !controller.signal.aborted && setLoading(false));
        }, 150);
        return () => {
            clearTimeout(timer);
            controller.abort();
        };
    }, [api, projectId, query]);

    const shown = result && result.kind === query.kind ? result : null;
    const controlProps = { api, projectId, events, onChange: onQueryChange };
    return (
        <section>
            <div className="toolbar">
                <div className="tabs" role="tablist">
                    {KINDS.map(({ kind, label }) => (
                        <button
                            key={kind}
                            role="tab"
                            aria-selected={query.kind === kind}
                            onClick={() => query.kind !== kind && onQueryChange(defaultQuery(kind, events))}
                        >
                            {label}
                        </button>
                    ))}
                </div>
                <span style={{ flex: 1 }} />
                {saving === null ? (
                    <button className="btn" onClick={() => setSaving(title ?? querySummary(query))}>
                        Save insight
                    </button>
                ) : (
                    <form
                        style={{ display: "flex", gap: 6 }}
                        onSubmit={(e) => {
                            e.preventDefault();
                            void onSave(saving, query).then(() => setSaving(null));
                        }}
                    >
                        <input className="btn" aria-label="Insight name" value={saving} onChange={(e) => setSaving(e.target.value)} autoFocus />
                        <button className="btn primary" type="submit" disabled={!saving.trim()}>
                            Save
                        </button>
                        <button className="btn ghost" type="button" onClick={() => setSaving(null)}>
                            Cancel
                        </button>
                    </form>
                )}
            </div>
            <div className="card">
                {query.kind === "trend" && <TrendControls {...controlProps} query={query} />}
                {query.kind === "funnel" && <FunnelControls {...controlProps} query={query} />}
                {query.kind === "retention" && <RetentionControls {...controlProps} query={query} />}
                {error && (
                    <div className="error" role="alert">
                        <strong>{error.code.replace("_", " ")}</strong> {error.message}
                        {error.suggestions.length > 0 && (
                            <span>
                                {" "}
                                Did you mean
                                {error.suggestions.map((s) => (
                                    <span className="suggestion" key={s}>
                                        {s}
                                    </span>
                                ))}
                            </span>
                        )}
                    </div>
                )}
                <div className="result-head">
                    <h2>{title ?? querySummary(query)}</h2>
                    <span className="status" aria-live="polite">
                        {loading ? "Running…" : elapsed !== null ? `${Math.round(elapsed)} ms` : ""}
                    </span>
                </div>
                {shown ? <Result result={shown} /> : !error && <div className="status">Loading…</div>}
            </div>
        </section>
    );
}
