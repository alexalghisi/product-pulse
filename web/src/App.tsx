import { useCallback, useEffect, useMemo, useState } from "react";
import { Api } from "./api/client";
import type { EventSummary, Insight, InsightQuery, Me } from "./api/types";
import { InsightEditor } from "./components/InsightEditor";
import { defaultQuery, querySummary } from "./state/draft";

function Login({ api, onLogin }: { api: Api; onLogin: () => void }) {
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [error, setError] = useState<string | null>(null);
    return (
        <form
            className="login card"
            onSubmit={(e) => {
                e.preventDefault();
                api.login(username, password).then(onLogin, (err: Error) => setError(err.message));
            }}
        >
            <div className="brand">product-pulse</div>
            <label className="field">
                Username
                <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
            </label>
            <label className="field">
                Password
                <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
            </label>
            {error && <div className="error">{error}</div>}
            <button className="btn primary" type="submit">
                Log in
            </button>
        </form>
    );
}

const defaultApi = new Api();

export function App({ api = defaultApi }: { api?: Api }) {
    const [me, setMe] = useState<Me | null>(null);
    const refresh = useCallback(() => void api.me().then(setMe), [api]);
    useEffect(refresh, [refresh]);

    if (!me) return null;
    if (!me.authenticated) return <Login api={api} onLogin={refresh} />;
    const project = me.projects?.[0];
    if (!project) return <div className="main">No projects yet. Create one with manage.py create_project.</div>;
    return <Workspace api={api} projectId={project.id} projectName={project.name} username={me.username ?? ""} />;
}

function Workspace({ api, projectId, projectName, username }: { api: Api; projectId: number; projectName: string; username: string }) {
    const [events, setEvents] = useState<EventSummary[] | null>(null);
    const [insights, setInsights] = useState<Insight[]>([]);
    const [query, setQuery] = useState<InsightQuery | null>(null);
    const [selected, setSelected] = useState<Insight | null>(null);

    useEffect(() => {
        void api.events(projectId).then((list) => {
            setEvents(list);
            setQuery(defaultQuery(initialKind(), list));
        });
        void api.insights(projectId).then(setInsights);
    }, [api, projectId]);

    const sorted = useMemo(() => [...insights].sort((a, b) => a.name.localeCompare(b.name)), [insights]);
    if (!events || !query) return <div className="main status">Loading events…</div>;

    return (
        <div className="app">
            <aside className="sidebar">
                <div className="brand">
                    product-pulse
                    <small>
                        {projectName} · {username}
                    </small>
                </div>
                <button
                    className="btn primary"
                    onClick={() => {
                        setSelected(null);
                        setQuery(defaultQuery("trend", events));
                    }}
                >
                    New insight
                </button>
                <nav>
                    <div className="nav-title">Saved insights</div>
                    <ul className="saved-list">
                        {sorted.length === 0 && <li className="status" style={{ padding: "0 8px" }}>Nothing saved yet</li>}
                        {sorted.map((insight) => (
                            <li key={insight.id}>
                                <button
                                    aria-current={selected?.id === insight.id}
                                    onClick={() => {
                                        setSelected(insight);
                                        setQuery(insight.query);
                                    }}
                                >
                                    {insight.name}
                                    <span className="meta">{querySummary(insight.query)}</span>
                                </button>
                            </li>
                        ))}
                    </ul>
                </nav>
            </aside>
            <main className="main">
                <InsightEditor
                    api={api}
                    projectId={projectId}
                    events={events}
                    query={query}
                    title={selected?.name}
                    onQueryChange={(q) => {
                        setQuery(q);
                        if (selected && q !== selected.query) setSelected(null);
                    }}
                    onSave={async (name, q) => {
                        const saved = await api.saveInsight(projectId, name, q);
                        setInsights((list) => [...list, saved]);
                        setSelected(saved);
                    }}
                />
            </main>
        </div>
    );
}

function initialKind(): InsightQuery["kind"] {
    const kind = new URLSearchParams(window.location.search).get("kind");
    return kind === "funnel" || kind === "retention" ? kind : "trend";
}
