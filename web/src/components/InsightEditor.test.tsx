import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Api } from "../api/client";
import type { InsightQuery } from "../api/types";
import { defaultQuery } from "../state/draft";
import { funnel, trend } from "../test/fixtures";
import { InsightEditor } from "./InsightEditor";

const events = [
    { name: "$pageview", count: 7, last_seen: "" },
    { name: "signed_up", count: 4, last_seen: "" },
    { name: "project_created", count: 3, last_seen: "" },
];

function json(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function Harness({ api }: { api: Api }) {
    const [query, setQuery] = useState<InsightQuery>(defaultQuery("trend", events));
    return <InsightEditor api={api} projectId={1} events={events} query={query} onQueryChange={setQuery} onSave={async () => {}} />;
}

it("runs the query and switches kinds", async () => {
    const calls: InsightQuery[] = [];
    const api = new Api(async (url, init) => {
        if (url.includes("/properties")) return json([{ key: "$browser", type: "string", seen_in: 1, examples: [] }]);
        const body = JSON.parse(String(init?.body)) as InsightQuery;
        calls.push(body);
        return json(body.kind === "funnel" ? funnel : trend);
    });
    render(<Harness api={api} />);
    expect(await screen.findAllByTestId("trend-line")).toHaveLength(2);
    await userEvent.click(screen.getByRole("tab", { name: "Funnel" }));
    expect(await screen.findAllByTestId("funnel-bar")).toHaveLength(3);
    expect(calls.at(-1)?.kind).toBe("funnel");
});

it("shows API errors with the suggested names", async () => {
    const api = new Api(async (url) =>
        url.includes("/properties")
            ? json([])
            : json({ error: "unknown_event", message: "No event named 'signup'.", suggestions: ["signed_up"] }, 400),
    );
    render(<Harness api={api} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("No event named 'signup'.");
    expect(alert).toHaveTextContent("signed_up");
});

it("never lets a slow older response replace a newer one", async () => {
    const resolvers: ((r: Response) => void)[] = [];
    const api = new Api((url, init) => {
        if (url.includes("/properties")) return Promise.resolve(json([]));
        const body = JSON.parse(String(init?.body)) as InsightQuery;
        return new Promise<Response>((resolve, reject) => {
            init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
            resolvers.push(() => resolve(json(body.kind === "funnel" ? funnel : trend)));
        });
    });
    render(<Harness api={api} />);
    await waitFor(() => expect(resolvers).toHaveLength(1));
    await userEvent.click(screen.getByRole("tab", { name: "Funnel" }));
    await waitFor(() => expect(resolvers).toHaveLength(2));
    resolvers[1]?.(new Response());
    expect(await screen.findAllByTestId("funnel-bar")).toHaveLength(3);
    resolvers[0]?.(new Response());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryAllByTestId("trend-line")).toHaveLength(0);
});
