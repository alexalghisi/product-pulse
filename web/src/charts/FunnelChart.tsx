import { useState } from "react";
import type { FunnelResult, FunnelStepResult } from "../api/types";
import { formatNumber, formatPercent, seriesLabel } from "../format";
import { seriesColor } from "./colors";
import { linear } from "./scale";
import { useWidth } from "./useWidth";

const HEIGHT = 300;
const MARGIN = { top: 28, right: 8, bottom: 44, left: 8 };

interface Group {
    key: string;
    label: string;
    color: string;
    steps: FunnelStepResult[];
}

function groups(result: FunnelResult): Group[] {
    if (result.breakdowns.length === 0) {
        return [{ key: "all", label: "All persons", color: seriesColor(0), steps: result.overall }];
    }
    return result.breakdowns.map((b, i) => ({
        key: b.breakdown_value ?? String(i),
        label: seriesLabel("", "total", b.breakdown_value),
        color: seriesColor(i, b.breakdown_value === "$other"),
        steps: b.steps,
    }));
}

/** Bars show conversion from step 1, so breakdown values of different size compare fairly. */
export function FunnelChart({ result }: { result: FunnelResult }) {
    const [ref, width] = useWidth<HTMLDivElement>();
    const [hover, setHover] = useState<{ group: number; step: number } | null>(null);
    const all = groups(result);
    const stepCount = result.overall.length;
    const slot = (width - MARGIN.left - MARGIN.right) / stepCount;
    const barGap = 2;
    const barWidth = Math.min(64, (slot * 0.7 - barGap * (all.length - 1)) / all.length);
    const y = linear([0, 1], [HEIGHT - MARGIN.bottom, MARGIN.top]);

    const hoveredGroup = hover ? all[hover.group] : undefined;
    const hoveredStep = hover && hoveredGroup ? hoveredGroup.steps[hover.step] : undefined;
    return (
        <div className="chart" ref={ref}>
            <svg width={width} height={HEIGHT} role="img" aria-label="Funnel chart">
                <line className="baseline" x1={MARGIN.left} x2={width - MARGIN.right} y1={y(0)} y2={y(0)} />
                {result.overall.map((step, s) => {
                    const cx = MARGIN.left + slot * s + slot / 2;
                    const groupWidth = all.length * barWidth + (all.length - 1) * barGap;
                    return (
                        <g key={s}>
                            {all.map((group, g) => {
                                const rate = group.steps[s]?.conversion_from_start ?? 0;
                                const x0 = cx - groupWidth / 2 + g * (barWidth + barGap);
                                const h = Math.max(y(0) - y(rate), rate > 0 ? 2 : 0);
                                return (
                                    <g key={group.key}>
                                        <rect
                                            x={x0}
                                            y={y(0) - (y(0) - y(1))}
                                            width={barWidth}
                                            height={y(0) - y(1)}
                                            fill="var(--surface-2)"
                                            rx={4}
                                        />
                                        <rect
                                            x={x0}
                                            y={y(0) - h}
                                            width={barWidth}
                                            height={h}
                                            fill={group.color}
                                            rx={4}
                                            data-testid="funnel-bar"
                                            onMouseEnter={() => setHover({ group: g, step: s })}
                                            onMouseLeave={() => setHover(null)}
                                        />
                                    </g>
                                );
                            })}
                            {all.length === 1 && (
                                <text x={cx} y={y(step.conversion_from_start) - 8} textAnchor="middle" className="bar-label" fill="var(--text-primary)" fontSize={12} fontWeight={600}>
                                    {formatPercent(step.conversion_from_start)}
                                </text>
                            )}
                            <text x={cx} y={HEIGHT - MARGIN.bottom + 18} textAnchor="middle" fill="var(--text-secondary)" fontSize={12}>
                                {s + 1}. {step.event}
                            </text>
                            <text x={cx} y={HEIGHT - MARGIN.bottom + 34} textAnchor="middle" fill="var(--text-muted)" fontSize={11}>
                                {formatNumber(step.persons)} persons
                            </text>
                        </g>
                    );
                })}
            </svg>
            {hover && hoveredGroup && hoveredStep && (
                <div
                    className="tooltip"
                    role="tooltip"
                    style={{ left: Math.min(MARGIN.left + slot * hover.step + slot / 2, width - 200), top: 0 }}
                >
                    <div className="row">
                        <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                            <span className="swatch" style={{ background: hoveredGroup.color }} />
                            {hoveredGroup.label}
                        </span>
                    </div>
                    <div className="row">
                        <span>{hoveredStep.event}</span>
                        <span>{formatNumber(hoveredStep.persons)}</span>
                    </div>
                    <div className="row">
                        <span>From start</span>
                        <span>{formatPercent(hoveredStep.conversion_from_start)}</span>
                    </div>
                    <div className="row">
                        <span>From previous</span>
                        <span>{formatPercent(hoveredStep.conversion_from_previous)}</span>
                    </div>
                </div>
            )}
            {all.length > 1 && (
                <div className="legend">
                    {all.map((g) => (
                        <span key={g.key}>
                            <span className="swatch" style={{ background: g.color }} />
                            {g.label}
                        </span>
                    ))}
                </div>
            )}
            <table className="data">
                <thead>
                    <tr>
                        <th>{all.length > 1 ? "Breakdown" : "Step"}</th>
                        {result.overall.map((s, i) => (
                            <th key={i}>
                                {i + 1}. {s.event}
                            </th>
                        ))}
                        <th>Conversion</th>
                    </tr>
                </thead>
                <tbody>
                    {all.map((g) => (
                        <tr key={g.key}>
                            <td>{g.label}</td>
                            {g.steps.map((s, i) => (
                                <td key={i}>
                                    {formatNumber(s.persons)}
                                    {i > 0 && <span className="status"> ({formatPercent(s.conversion_from_previous)})</span>}
                                </td>
                            ))}
                            <td>{formatPercent(g.steps[stepCount - 1]?.conversion_from_start ?? 0)}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}
