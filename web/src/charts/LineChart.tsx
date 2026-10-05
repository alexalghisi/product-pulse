import { useState } from "react";
import type { Interval } from "../api/types";
import { formatBucket, formatNumber } from "../format";
import { labelIndices, linear, niceTicks } from "./scale";
import { useWidth } from "./useWidth";

export interface Line {
    key: string;
    label: string;
    color: string;
    data: number[];
}

const HEIGHT = 280;
const MARGIN = { top: 12, right: 16, bottom: 28, left: 48 };

export function LineChart({
    buckets,
    interval,
    lines,
    partialLast = false,
}: {
    buckets: string[];
    interval: Interval;
    lines: Line[];
    /** Draw the segment into the last bucket dashed: it is still filling up. */
    partialLast?: boolean;
}) {
    const [ref, width] = useWidth<HTMLDivElement>();
    const [hover, setHover] = useState<number | null>(null);
    const max = Math.max(0, ...lines.flatMap((l) => l.data));
    const ticks = niceTicks(max);
    const top = ticks[ticks.length - 1] ?? 1;
    const x = linear([0, Math.max(buckets.length - 1, 1)], [MARGIN.left, width - MARGIN.right]);
    const y = linear([0, top], [HEIGHT - MARGIN.bottom, MARGIN.top]);
    const labelled = labelIndices(buckets.length, Math.max(2, Math.floor(width / 90)));

    function onMove(event: React.MouseEvent<SVGRectElement>) {
        const rect = event.currentTarget.getBoundingClientRect();
        const relative = (event.clientX - rect.left) / rect.width;
        setHover(Math.round(relative * (buckets.length - 1)));
    }

    const hovered = hover !== null ? buckets[hover] : undefined;
    return (
        <div className="chart" ref={ref}>
            <svg width={width} height={HEIGHT} role="img" aria-label="Trend chart">
                <g className="grid">
                    {ticks.map((t) => (
                        <line key={t} x1={MARGIN.left} x2={width - MARGIN.right} y1={y(t)} y2={y(t)} />
                    ))}
                </g>
                <g className="axis">
                    {ticks.map((t) => (
                        <text key={t} x={MARGIN.left - 8} y={y(t) + 4} textAnchor="end">
                            {formatNumber(t)}
                        </text>
                    ))}
                    {labelled.map((i) => (
                        <text key={i} x={x(i)} y={HEIGHT - 8} textAnchor="middle">
                            {formatBucket(buckets[i] ?? "", interval)}
                        </text>
                    ))}
                </g>
                <line className="baseline" x1={MARGIN.left} x2={width - MARGIN.right} y1={y(0)} y2={y(0)} />
                {lines.map((line) => {
                    const solid = partialLast && line.data.length > 1 ? line.data.length - 1 : line.data.length;
                    const point = (i: number) => `${x(i).toFixed(1)},${y(line.data[i] ?? 0).toFixed(1)}`;
                    return (
                        <g key={line.key}>
                            <path
                                d={line.data.slice(0, solid).map((_, i) => `${i ? "L" : "M"}${point(i)}`).join("")}
                                fill="none"
                                stroke={line.color}
                                strokeWidth={2}
                                strokeLinejoin="round"
                                strokeLinecap="round"
                                data-testid="trend-line"
                            />
                            {solid < line.data.length && (
                                <path
                                    d={`M${point(solid - 1)}L${point(solid)}`}
                                    fill="none"
                                    stroke={line.color}
                                    strokeWidth={2}
                                    strokeDasharray="4 4"
                                    data-testid="partial-segment"
                                />
                            )}
                        </g>
                    );
                })}
                {hover !== null && (
                    <g>
                        <line x1={x(hover)} x2={x(hover)} y1={MARGIN.top} y2={HEIGHT - MARGIN.bottom} stroke="var(--text-muted)" strokeDasharray="3 3" />
                        {lines.map((line) => (
                            <circle key={line.key} cx={x(hover)} cy={y(line.data[hover] ?? 0)} r={4} fill={line.color} stroke="var(--surface-1)" strokeWidth={2} />
                        ))}
                    </g>
                )}
                <rect
                    x={MARGIN.left}
                    y={MARGIN.top}
                    width={Math.max(0, width - MARGIN.left - MARGIN.right)}
                    height={HEIGHT - MARGIN.top - MARGIN.bottom}
                    fill="transparent"
                    onMouseMove={onMove}
                    onMouseLeave={() => setHover(null)}
                />
            </svg>
            {hover !== null && hovered && (
                <div
                    className="tooltip"
                    role="tooltip"
                    style={{ left: Math.min(x(hover) + 12, width - 200), top: MARGIN.top }}
                >
                    <div className="status">{formatBucket(hovered, interval)}</div>
                    {[...lines]
                        .sort((a, b) => (b.data[hover] ?? 0) - (a.data[hover] ?? 0))
                        .slice(0, 8)
                        .map((line) => (
                            <div className="row" key={line.key}>
                                <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                                    <span className="swatch" style={{ background: line.color }} />
                                    {line.label}
                                </span>
                                <span>{formatNumber(line.data[hover] ?? 0)}</span>
                            </div>
                        ))}
                </div>
            )}
            {lines.length > 1 && (
                <div className="legend">
                    {lines.map((line) => (
                        <span key={line.key}>
                            <span className="swatch" style={{ background: line.color }} />
                            {line.label}
                        </span>
                    ))}
                </div>
            )}
        </div>
    );
}
