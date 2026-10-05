# product-pulse

A small, self-hostable product analytics tool that people and agents can both use. Apps send events to a capture endpoint. The events land in ClickHouse, attributed to persons even across the anonymous-to-signed-up boundary. You ask trends, funnel and retention questions in a web app, over a REST API or through an MCP server. The agent side is the part I cared most about: tool descriptions written for a model, results sized for a context window, errors that tell the caller what to try next, a reference document written for machines, and an eval suite that measures whether a model actually gets the right number.

![Trends with a breakdown, a funnel by browser, retention, then an MCP client calling the tools](docs/preview.gif)

The GIF shows the real app running on about 2M seeded events, then `scripts/mcp_demo.py` connecting to the MCP server over stdio. It lists events, gets an unknown name wrong, follows the suggestion in the error, inspects properties and runs a funnel.

## How it works

```
SDK ──POST /capture──▶ Django/DRF ──XADD──▶ Redis stream ──▶ writer ──▶ ClickHouse (events, person_distinct_id)
                         │                                    │
                         └ validate, token → project          └ Postgres: persons, $identify merges

web (React) ─┐
REST API ────┼──▶ AnalyticsService (one project) ──▶ query compiler ──▶ parameterized SQL ──▶ ClickHouse
MCP server ──┘                                                          Postgres: projects, keys, insights, dashboards
```

### Ingestion

- **`/capture`** (`ingestion/views.py`) takes a single event, a `{"api_key", "batch"}` envelope or a bare list. Bodies can be gzip'd. Decompression is capped at 20 MB, so a 20 KB gzip bomb is refused without being inflated (`ingestion/envelope.py`). The project token is looked up once a minute, not per request.
- **Validation is per event** (`ingestion/events.py`). One bad event in a batch of 500 is reported back by index and the other 499 are accepted. Placeholder ids such as `"null"`, `"undefined"` or `"anonymous"` are rejected, because accepting them would merge every affected user into one person. Timestamps more than an hour in the future are rejected too.
- **Buffering** (`ingestion/buffer.py`). Each accepted batch is one entry in a Redis stream. Writers read through a consumer group and acknowledge an entry only after ClickHouse has its rows. An entry a crashed writer left pending is reclaimed after 60 s, and an entry that keeps failing goes to a dead-letter stream after five deliveries. When a read of 50 entries fails, the writer retries them one at a time, so a single poison entry doesn't hold back the rest (`ingestion/worker.py`). Above a configurable backlog, capture answers `503` with `Retry-After` instead of letting Redis grow until it runs out of memory.
- **Idempotency.** `events` is a `ReplacingMergeTree` whose sorting key ends in the event uuid, and queries read `FINAL`. An SDK retry with the same uuid and timestamp is collapsed. A test captures the same batch twice through the HTTP endpoint and the real stream, then counts 5 rows, not 10.
- **Persons** (`ingestion/persons.py`). Each new `distinct_id` gets an anonymous person. `$identify` with `$anon_distinct_id` links the two ids, merging the anonymous person into the identified one when both already exist. Two identified persons are never merged: that usually means a shared device. A second user logging in on that device gets their own person. Each project is resolved under a Postgres advisory lock. Mappings go to ClickHouse as versioned rows in `person_distinct_id`, and queries join on the latest version. A merge therefore never rewrites events, and a user's earlier anonymous events count for them as soon as they sign up. Every batch re-sends the current mappings for its ids, so a batch that failed after Postgres committed still writes them on retry.

### Queries

There is no free-form SQL. A query is one of three pydantic models (`query/models.py`): a trend (count, unique persons, or sum/avg/min/max of a numeric property, by hour/day/week/month, with an optional breakdown), a funnel (2–10 ordered steps with a conversion window and breakdown) or a retention table (cohorts by first start event, return event per later period). Unknown fields are an error, not ignored, so a typo can't silently widen a query.

`query/compiler.py` turns them into ClickHouse SQL under two rules, and the tests check both:

1. **No interpolation.** Every event name, property key, filter value, limit and date is bound with ClickHouse's server-side parameters (`{p3:String}` in the SQL, `param_p3=...` on the request). The SQL text is built only from constants in the compiler. A test pushes `x') OR 1=1 UNION SELECT * FROM system.users --` through every field of every query kind. It checks that the payload never appears in the SQL, that every placeholder has a bound value, and that no table other than ours is named.
2. **Tenant isolation in one place.** Tables are named only in `Scope`, and every read it produces carries `project_id = {project_id:UInt64}`. The project id is an argument supplied by the caller's authentication, never a field of the query. Tests check that every `FROM events` and `FROM person_distinct_id` in every compiled query is scoped. They also run each query kind against a second project with the same event names and `distinct_id`s and assert the results don't move. A filter on a property literally named `project_id` doesn't move them either.

Some semantics are easy to get wrong, so I wrote them down and tested them against hand-computed answers (`tests/test_queries_clickhouse.py`):

- A trend's `total` comes from a second grouping set, not from adding up buckets. A sum of daily unique persons is not the number of unique persons over the range.
- In a funnel, step 1 must fall inside the date range. Later steps may land up to the window after it ends, otherwise people who started on the last day could never convert. A funnel breakdown takes the property from the person's first step-1 event, so each person has exactly one value and folding the long tail into `$other` stays exact.
- In retention, periods that haven't started yet are `null`, not `0`.

### API, web, saved work

- **REST API** (`api/views.py`). Personal API keys (`ppk_...`, stored hashed) are bound to one user and one project. The web app uses session login with CSRF. Projects you can't see answer 404, so ids can't be probed.
- **`AnalyticsService`** (`analytics/service.py`) is the layer both the API and MCP call. It checks event and property names against what the project actually has before running anything. A typo comes back as `unknown_event` with the closest real names, rather than as a chart of zeros.
- **Web app** (`web/`). React 19, TypeScript, Vite, no UI or chart library. The trend, funnel and retention views are small SVG/table components with hover tooltips and legends. "Other" is always neutral grey, and the bucket that is still filling up (today) is drawn dashed. Every edit re-runs the query and aborts the previous request, and a test checks that a slow older response can't replace a newer one. Insights are saved to Postgres. Dashboards exist in the API but have no screen yet.

### For agents

- **MCP server** (`mcp_server/`) uses the official `mcp` SDK, over stdio or streamable HTTP. The tools are `list_events`, `get_event_properties`, `run_trend`, `run_funnel`, `run_retention`, `create_insight` and `list_insights`. No tool takes a project id: over stdio the project comes from `PULSE_API_KEY`, and over HTTP from the bearer token, checked by a `TokenVerifier`.
- **Descriptions and schemas are written for a model.** Examples go inside parameter descriptions. The difference between `total` and `unique_persons` is stated where a model chooses between them. Server instructions give the order of operations.
- **Results are trimmed to a budget** (`mcp_server/formatting.py`). Above 400 numbers, a trend drops the per-bucket series, keeps the exact totals and says so in `notes`, including how to get the detail back. Event lists, property lists, breakdowns and cohorts are capped the same way.
- **Errors are useful.** An unknown event returns `No event named 'signup' in this project (names are case-sensitive). Did you mean: signed_up?`. Invalid dates name the accepted formats. Invalid arguments name the field.
- **[`docs/agents.md`](docs/agents.md)** is the interface reference written for machines. It lists each tool's exact semantics, a question-shape-to-tool table, the error codes with what to do about each, the REST endpoints and the capture format.
- **Evals** (`evals/`). There are 19 natural-language questions over a dataset whose answers are known by construction. `evals/fixture.py` documents the rules that produce every expected number, so the evals check the query engine as well as the agent. A test computes all 19 through the MCP tools and compares them with the hand-derived values. The runner drives the real MCP tools through a pluggable model client and scores tool selection and numeric correctness. With `PULSE_EVAL_API_KEY` or `OPENAI_API_KEY` set it uses an OpenAI-compatible Chat Completions client that passes the MCP schemas through unchanged. Without a key it uses a deterministic offline planner, so the suite runs in CI.

**What the offline planner can and can't do.** It is a keyword baseline, not an agent. It discovers events and properties through the tools, then maps month names, "within N days", "following week", "how many people" and property values that appear verbatim in the question to a single query. It scores **16/19** (tool selection 19/19). It misses three questions that are in the suite on purpose:

- A paraphrase: "send a note to someone else" means `note_shared`. The planner matched "share" in "what share of" to `note_shared` instead and built the funnel backwards.
- A question that needs two queries and a subtraction.
- A question that needs reasoning over a breakdown ("the source with the most sign-ups").

The test suite pins exactly these three misses. If a change fixes or breaks a question, the test says which. The OpenAI-compatible client is tested against a scripted fake endpoint. That test covers the tool loop, malformed tool arguments and an unknown-event error the model then corrects. There is no API key in this environment, so I have **no measured score for a real model** to report.

## Measured

On a 2-vCPU VM shared with other builds. Client, server, Postgres, Redis and ClickHouse were all on the same machine. Raw results are in `bench/results/`.

**Ingestion** (`bench/ingest.py`): 200,000 events in gzip'd batches of 100, from 4 concurrent clients, to gunicorn with 2 sync workers.

| Stage | Throughput | |
| --- | --- | --- |
| `/capture` → Redis | 20,352 events/s | per request p50 14.8 ms, p95 38.9 ms |
| writer: Redis → persons → ClickHouse | 13,596 events/s | one writer process, 0 failed writes; 200,000 rows in ClickHouse afterwards |
| `seed_demo` through the same processor | ~18,000 events/s | 1,955,662 events in 107 s |

**Query latency** (`bench/query.py`): on the seeded demo project, 1,955,662 events over 90 days, ClickHouse `max_threads=2`. The timings are for `AnalyticsService.run`: name validation (one or two discovery queries), compilation, execution and shaping. HTTP overhead is excluded. 15 runs after 2 warm-ups.

| Query | p50 | p95 |
| --- | --- | --- |
| trend: daily pageviews, 90 days | 91 ms | 123 ms |
| trend: daily unique persons, 90 days | 364 ms | 389 ms |
| trend: pageviews by `$browser`, 90 days | 789 ms | 882 ms |
| trend: hourly, 7 days | 50 ms | 69 ms |
| funnel: 4 steps, 14-day window, 90 days | 144 ms | 156 ms |
| funnel: 4 steps by `$browser` | 478 ms | 531 ms |
| retention: weekly, 12 cohorts | 291 ms | 348 ms |

Breakdowns are the slow case. They parse the JSON `properties` string of about 1M pageviews twice: once to find the top values, once to group. Materialized columns for hot properties would be the next step.

## Tech stack

Python 3.13, Django 5.2, Django REST framework, pydantic 2, ClickHouse (HTTP interface, server-side parameters), PostgreSQL 16, Redis streams, the `mcp` Python SDK (2.x), httpx. React 19, TypeScript (strict, `noUncheckedIndexedAccess`), Vite, Vitest, Testing Library. pytest, pytest-django, mypy `--strict` with django-stubs and djangorestframework-stubs, ruff. Playwright and Pillow for the preview.

## Running it

You need PostgreSQL, Redis and a ClickHouse binary.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

CLICKHOUSE_BIN=/path/to/clickhouse scripts/dev-clickhouse.sh &   # HTTP on 127.0.0.1:8130
export PULSE_DATABASE_URL=postgres://user:pass@localhost:5432/ph_pulse
python manage.py migrate
python manage.py clickhouse_migrate
python manage.py seed_demo                 # ~2M events; prints a project token, a personal key, login demo/demo

python manage.py runserver 127.0.0.1:8140  # API and /capture
python manage.py run_worker                # Redis stream -> ClickHouse
cd web && npm ci && npm run dev            # http://127.0.0.1:5280, proxies /api to 8140
```

Agents:

```bash
PULSE_API_KEY=ppk_... pulse-mcp                       # stdio
pulse-mcp --transport http --port 8150                # streamable HTTP, Authorization: Bearer ppk_...
PULSE_API_KEY=ppk_... python scripts/mcp_demo.py      # the session from the GIF
pulse-evals --model offline                           # or set OPENAI_API_KEY (PULSE_EVAL_MODEL, PULSE_EVAL_BASE_URL)
```

`python manage.py create_project NAME` creates an empty project with a key, and `/capture` takes the printed project token. Settings are environment variables in `settings.py`: `PULSE_DATABASE_URL`, `PULSE_CLICKHOUSE_URL`, `PULSE_CLICKHOUSE_DATABASE`, `PULSE_REDIS_URL`, `PULSE_STREAM`, `PULSE_STREAM_HIGH_WATERMARK`.

## Tests

```bash
ruff check . && mypy && pytest     # 116 tests; ClickHouse, Postgres and Redis tests run against real servers
cd web && npm run typecheck && npm test                              # 21 tests
```

The Python tests use a Django test database on PostgreSQL and a throwaway ClickHouse database per run. Tests that need ClickHouse or Redis are skipped, with the reason, when the server isn't reachable. `.github/workflows/ci.yml` defines the same steps with service containers, plus the offline evals with an 80% threshold. I haven't run that workflow; locally everything ran against ClickHouse 26.10.

## Known limitations

- Times are UTC only. Filters and breakdowns work on event properties; there are no person properties yet.
- Person merging covers `$identify` only: no aliasing API, no un-merge.
- Queries read `events FINAL` for exact deduplication. That costs time on very large ranges. A production setup would run merges or deduplicate at write time instead.
- Several writers can share the consumer group, and the advisory lock makes that safe, but the benchmark used one.
- The web app shows the first project the user belongs to. Dashboards can be created and read through the API only.

## Layout

```
src/product_pulse/
  ingestion/     capture view, envelope decoding, validation, Redis stream buffer, persons, writer
  query/         query models, date handling, SQL compiler, runner and result shapes
  analytics/     project-scoped service shared by the API and MCP: discovery, validation, insights
  api/           REST API and personal-key authentication
  mcp_server/    MCP tools, result formatting, stdio/HTTP entry point
  evals/         eval fixture, questions, harness, offline planner, OpenAI-compatible client
  seed/          synthetic product data generator
  clickhouse/    HTTP client and table definitions
  core/          Django models and management commands
web/             React app
bench/           ingestion and query benchmarks, results/
docs/            agents.md, preview.gif
scripts/         local ClickHouse, MCP demo, preview builder
tests/           pytest suite
```

## License

MIT
