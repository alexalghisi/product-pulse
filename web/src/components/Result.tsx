import type { QueryResult } from "../api/types";
import { colorTrendLines, lastBucketIsPartial } from "./trendLines";
import { FunnelChart } from "../charts/FunnelChart";
import { LineChart } from "../charts/LineChart";
import { RetentionTable } from "../charts/RetentionTable";
import { formatNumber } from "../format";

export function Result({ result }: { result: QueryResult }) {
    switch (result.kind) {
        case "trend": {
            const lines = colorTrendLines(result);
            return (
                <>
                    <LineChart buckets={result.buckets} interval={result.interval} lines={lines} partialLast={lastBucketIsPartial(result)} />
                    <table className="data">
                        <thead>
                            <tr>
                                <th>Series</th>
                                <th>Total over range</th>
                            </tr>
                        </thead>
                        <tbody>
                            {lines.map((line, i) => (
                                <tr key={line.key}>
                                    <td>
                                        <span className="swatch" style={{ background: line.color, marginRight: 6 }} />
                                        {line.label}
                                    </td>
                                    <td>{formatNumber(result.series[i]?.total ?? 0)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </>
            );
        }
        case "funnel":
            return <FunnelChart result={result} />;
        case "retention":
            return <RetentionTable result={result} />;
    }
}
