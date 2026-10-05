import type { RetentionResult } from "../api/types";
import { formatNumber, formatPercent } from "../format";
import { sequentialStep } from "./colors";

export function RetentionTable({ result }: { result: RetentionResult }) {
    const periods = result.cohorts[0]?.retained.length ?? 0;
    const unit = result.period === "day" ? "Day" : "Week";
    return (
        <table className="data retention">
            <thead>
                <tr>
                    <th>Cohort</th>
                    <th>Persons</th>
                    {Array.from({ length: periods }, (_, i) => (
                        <th key={i} style={{ textAlign: "center" }}>
                            {unit} {i + 1}
                        </th>
                    ))}
                </tr>
            </thead>
            <tbody>
                {result.cohorts.map((cohort) => (
                    <tr key={cohort.cohort}>
                        <td>{cohort.cohort}</td>
                        <td>{formatNumber(cohort.size)}</td>
                        {cohort.rates.map((rate, i) => {
                            if (rate === null) return <td key={i} className="cell" aria-label="not yet" />;
                            const step = sequentialStep(rate);
                            return (
                                <td
                                    key={i}
                                    className="cell"
                                    title={`${formatNumber(cohort.retained[i] ?? 0)} of ${formatNumber(cohort.size)} persons`}
                                    style={{
                                        background: `var(--seq-${step})`,
                                        color: step >= 4 ? "#ffffff" : "var(--text-primary)",
                                    }}
                                >
                                    {formatPercent(rate)}
                                </td>
                            );
                        })}
                    </tr>
                ))}
            </tbody>
        </table>
    );
}
