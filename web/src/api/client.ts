import type {
    ApiErrorBody,
    EventSummary,
    Insight,
    InsightQuery,
    Me,
    PropertySummary,
    QueryResult,
} from "./types";

export class ApiError extends Error {
    readonly status: number;
    readonly code: string;
    readonly suggestions: string[];

    constructor(status: number, body: Partial<ApiErrorBody>) {
        super(body.message ?? `Request failed (${status})`);
        this.status = status;
        this.code = body.error ?? "http_error";
        this.suggestions = body.suggestions ?? [];
    }
}

function csrfToken(): string {
    const match = /(?:^|;\s*)csrftoken=([^;]+)/.exec(document.cookie);
    return match?.[1] ? decodeURIComponent(match[1]) : "";
}

export type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

export class Api {
    private readonly fetchImpl: Fetch;

    constructor(fetchImpl: Fetch = (input, init) => fetch(input, init)) {
        this.fetchImpl = fetchImpl;
    }

    private async request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
        const headers: Record<string, string> = { Accept: "application/json" };
        if (body !== undefined) headers["Content-Type"] = "application/json";
        if (method !== "GET") headers["X-CSRFToken"] = csrfToken();
        const response = await this.fetchImpl(`/api/${path}`, {
            method,
            headers,
            credentials: "same-origin",
            body: body === undefined ? undefined : JSON.stringify(body),
            signal,
        });
        if (response.status === 204) return undefined as T;
        const data: unknown = await response.json().catch(() => ({}));
        if (!response.ok) throw new ApiError(response.status, data as Partial<ApiErrorBody>);
        return data as T;
    }

    me(): Promise<Me> {
        return this.request("GET", "auth/me");
    }

    login(username: string, password: string): Promise<{ username: string }> {
        return this.request("POST", "auth/login", { username, password });
    }

    logout(): Promise<void> {
        return this.request("POST", "auth/logout");
    }

    events(projectId: number): Promise<EventSummary[]> {
        return this.request("GET", `projects/${projectId}/events`);
    }

    properties(projectId: number, event: string): Promise<PropertySummary[]> {
        return this.request("GET", `projects/${projectId}/properties?event=${encodeURIComponent(event)}`);
    }

    query(projectId: number, query: InsightQuery, signal?: AbortSignal): Promise<QueryResult> {
        return this.request("POST", `projects/${projectId}/query`, query, signal);
    }

    insights(projectId: number): Promise<Insight[]> {
        return this.request("GET", `projects/${projectId}/insights`);
    }

    saveInsight(projectId: number, name: string, query: InsightQuery): Promise<Insight> {
        return this.request("POST", `projects/${projectId}/insights`, { name, query });
    }

    deleteInsight(projectId: number, id: number): Promise<void> {
        return this.request("DELETE", `projects/${projectId}/insights/${id}`);
    }
}
