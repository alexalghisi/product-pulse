// Mirrors product_pulse/query/models.py and runner.py. The API publishes the full
// JSON schema at /api/schema/query; these are the parts the UI builds and reads.

export type Operator =
    | "eq"
    | "neq"
    | "contains"
    | "not_contains"
    | "gt"
    | "gte"
    | "lt"
    | "lte"
    | "is_set"
    | "is_not_set";

export interface PropertyFilter {
    key: string;
    operator: Operator;
    value?: string | number | string[];
}

export interface DateRange {
    date_from: string;
    date_to?: string | null;
}

export type Math = "total" | "unique_persons" | "sum" | "avg" | "min" | "max";
export type Interval = "hour" | "day" | "week" | "month";

export interface TrendSeries {
    event: string;
    math: Math;
    math_property?: string | null;
    filters: PropertyFilter[];
}

export interface TrendQuery {
    kind: "trend";
    series: TrendSeries[];
    date_range: DateRange;
    interval: Interval;
    breakdown?: string | null;
    breakdown_limit?: number;
    filters: PropertyFilter[];
}

export interface FunnelQuery {
    kind: "funnel";
    steps: { event: string; filters: PropertyFilter[] }[];
    conversion_window: number;
    conversion_window_unit: "minute" | "hour" | "day" | "week";
    date_range: DateRange;
    breakdown?: string | null;
    breakdown_limit?: number;
    filters: PropertyFilter[];
}

export interface RetentionQuery {
    kind: "retention";
    start_event: { event: string; filters: PropertyFilter[] };
    return_event: { event: string; filters: PropertyFilter[] };
    period: "day" | "week";
    periods: number;
    date_range: DateRange;
    filters: PropertyFilter[];
}

export type InsightQuery = TrendQuery | FunnelQuery | RetentionQuery;
export type InsightKind = InsightQuery["kind"];

export interface TrendLine {
    event: string;
    math: string;
    breakdown_value: string | null;
    data: number[];
    total: number;
}

export interface TrendResult {
    kind: "trend";
    interval: Interval;
    date_from: string;
    date_to: string;
    buckets: string[];
    series: TrendLine[];
}

export interface FunnelStepResult {
    event: string;
    persons: number;
    conversion_from_start: number;
    conversion_from_previous: number;
}

export interface FunnelResult {
    kind: "funnel";
    date_from: string;
    date_to: string;
    conversion_window: string;
    overall: FunnelStepResult[];
    breakdowns: { breakdown_value: string | null; steps: FunnelStepResult[] }[];
}

export interface RetentionCohort {
    cohort: string;
    size: number;
    retained: (number | null)[];
    rates: (number | null)[];
}

export interface RetentionResult {
    kind: "retention";
    period: "day" | "week";
    cohorts: RetentionCohort[];
}

export type QueryResult = TrendResult | FunnelResult | RetentionResult;

export interface EventSummary {
    name: string;
    count: number;
    last_seen: string;
}

export interface PropertySummary {
    key: string;
    type: string;
    seen_in: number;
    examples: string[];
}

export interface Insight {
    id: number;
    name: string;
    description: string;
    query: InsightQuery;
    created_at: string;
    updated_at: string;
}

export interface Me {
    authenticated: boolean;
    username?: string;
    projects?: { id: number; name: string }[];
}

export interface ApiErrorBody {
    error: string;
    message: string;
    suggestions: string[];
}
