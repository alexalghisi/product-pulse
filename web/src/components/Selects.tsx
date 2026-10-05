import { useEffect, useState } from "react";
import type { Api } from "../api/client";
import type { EventSummary, PropertySummary } from "../api/types";
import { formatNumber } from "../format";

export function EventSelect({
    label,
    value,
    events,
    onChange,
}: {
    label: string;
    value: string;
    events: EventSummary[];
    onChange: (event: string) => void;
}) {
    const known = events.some((e) => e.name === value);
    return (
        <label className="field">
            {label}
            <select value={value} onChange={(e) => onChange(e.target.value)}>
                {!known && <option value={value}>{value}</option>}
                {events.map((e) => (
                    <option key={e.name} value={e.name}>
                        {e.name} ({formatNumber(e.count)})
                    </option>
                ))}
            </select>
        </label>
    );
}

const cache = new Map<string, Promise<PropertySummary[]>>();

export function usePropertyKeys(api: Api, projectId: number, event: string): string[] {
    const [keys, setKeys] = useState<string[]>([]);
    useEffect(() => {
        let live = true;
        const id = `${projectId}:${event}`;
        let pending = cache.get(id);
        if (!pending) {
            pending = api.properties(projectId, event).catch(() => []);
            cache.set(id, pending);
        }
        void pending.then((props) => live && setKeys(props.map((p) => p.key)));
        return () => {
            live = false;
        };
    }, [api, projectId, event]);
    return keys;
}

export function BreakdownSelect({
    value,
    keys,
    onChange,
}: {
    value: string | null | undefined;
    keys: string[];
    onChange: (key: string | null) => void;
}) {
    return (
        <label className="field">
            Breakdown
            <select value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
                <option value="">None</option>
                {value && !keys.includes(value) && <option value={value}>{value}</option>}
                {keys.map((k) => (
                    <option key={k} value={k}>
                        {k}
                    </option>
                ))}
            </select>
        </label>
    );
}

export const RANGES = [
    { value: "-7d", label: "Last 7 days" },
    { value: "-30d", label: "Last 30 days" },
    { value: "-90d", label: "Last 90 days" },
    { value: "-8w", label: "Last 8 weeks" },
];

export function RangeSelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
    const known = RANGES.some((r) => r.value === value);
    return (
        <label className="field">
            Date range
            <select value={value} onChange={(e) => onChange(e.target.value)}>
                {!known && <option value={value}>{value}</option>}
                {RANGES.map((r) => (
                    <option key={r.value} value={r.value}>
                        {r.label}
                    </option>
                ))}
            </select>
        </label>
    );
}
