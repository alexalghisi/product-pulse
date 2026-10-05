# product-pulse for agents

This document is for a model or a program that queries product-pulse. It states what
each interface accepts, what it returns and what the numbers mean. People should start
with the README.

## Interfaces

| Interface | Address | Credential |
| --- | --- | --- |
| MCP, stdio | `pulse-mcp` (or `python -m product_pulse.mcp_server`) | env `PULSE_API_KEY=ppk_...` |
| MCP, streamable HTTP | `http://HOST:8150/mcp` (`pulse-mcp --transport http`) | header `Authorization: Bearer ppk_...` |
| REST | `http://HOST:8140/api/...` | header `Authorization: Bearer ppk_...` |
| Ingestion | `POST http://HOST:8140/capture` | project token `pp_...` in the body (`api_key`) |

A personal key (`ppk_`) is bound to exactly one project. No call takes a project id;
the project is always the key's. A project token (`pp_`) can only send events.

Client configuration for stdio:

```json
{
  "mcpServers": {
    "product-pulse": {
      "command": "pulse-mcp",
      "env": { "PULSE_API_KEY": "ppk_...", "PULSE_DATABASE_URL": "postgres://...", "PULSE_CLICKHOUSE_URL": "http://127.0.0.1:8130" }
    }
  }
}
```

## Recommended sequence

1. `list_events` - exact event names, counts, last seen. Names are case-sensitive.
2. `get_event_properties(event)` - property keys on that event, JSON type, share of
   events that have it (`seen_in`, 0-1), up to five common values.
3. One query tool, chosen by the question:

| Question shape | Tool | Read |
| --- | --- | --- |
| "How many times did X happen" | `run_trend`, `math: total` | `series[].total` |
| "How many people/users did X" | `run_trend`, `math: unique_persons` | `series[].total` |
| "Total/average of a numeric property" | `run_trend`, `math: sum` or `avg`, `math_property` | `series[].total` |
| "Which value of P has the most X" | `run_trend` with `breakdown: P` | series sorted by total, first is largest |
| "What share of people who did A then did B" | `run_funnel` | `overall[i].conversion_from_start` |
| "Which step loses the most people" | `run_funnel` | lowest `conversion_from_previous` |
| "Do people come back after A" | `run_retention` | `cohorts[].rates` |

4. On an error, read the message. Unknown names come with suggestions
   (`Did you mean: signed_up?`); retry with a suggested name.

## Tools

All tools return JSON objects (MCP structured content, also serialized as text).

### list_events()

`{"events": [{"name", "count", "last_seen"}], "total_events", "truncated"?}`. Covers the
last 365 days, most frequent first, at most 100 names.

### get_event_properties(event)

`{"event", "properties": [{"key", "type", "seen_in", "examples"}], "truncated"?}`.
Computed over a sample of at most 50,000 recent events of that name.

### run_trend(series, date_from="-30d", date_to=null, interval="day", breakdown=null, breakdown_limit=10, filters=null)

- `series`: 1-5 items of `{"event", "math", "math_property"?, "filters"?}`.
  `math` is one of `total`, `unique_persons`, `sum`, `avg`, `min`, `max`; the last four
  need `math_property` (a numeric property; numeric strings are parsed).
- Returns `{"buckets": [...], "series": [{"event", "math", "breakdown_value"?, "total", "data": [...]}], "notes"?}`.
- `total` is computed over the whole range, not summed from buckets: for
  `unique_persons` a person active on 5 days counts once.
- When `buckets x series > 400`, `buckets` and `data` are omitted and `notes` says so;
  totals are still exact.
- At most 1,000 buckets (e.g. `interval: hour` over 90 days is rejected; use `day`).

### run_funnel(steps, conversion_window=14, conversion_window_unit="day", date_from="-30d", date_to=null, breakdown=null, step_filters=null, filters=null)

- `steps`: 2-10 event names in order.
- A person enters at step 1 if they did it inside the date range. Later steps must
  follow, in order, within the window measured from step 1; they may fall after
  `date_to`.
- `step_filters`: `{"0": [filter, ...]}` applies filters to one step (0-based).
- `breakdown`: each person is counted under the property value on their first step-1
  event. Values beyond 10 are folded into `$other`.
- Returns `overall` and, with a breakdown, `breakdowns`: lists of
  `{"event", "persons", "conversion_from_start", "conversion_from_previous"}`. Rates are
  fractions (0.4123 = 41.23%).

### run_retention(start_event, return_event, period="week", periods=8, date_from="-8w", date_to=null, filters=null)

- A person's cohort is the first period in the range in which they did `start_event`.
- `retained[k-1]` is how many of them did `return_event` in period k after the cohort
  period; `rates[k-1]` is that divided by `size`. `null` means period k has not started.
- Weeks start on Monday.

### create_insight(name, query, description="")

Saves a query for people to see in the web app. `query` is the full query object:
`{"kind": "trend" | "funnel" | "retention", ...}`, the shape published as JSON Schema at
`GET /api/schema/query`. Note that in this form dates go in
`"date_range": {"date_from": ..., "date_to": ...}` and funnel steps are objects
(`{"event": "signed_up"}`). Names are validated before saving.

### list_insights()

`{"insights": [{"id", "name", "description", "query", "created_at", "updated_at"}]}`.

## Shared definitions

- **Dates.** UTC. `YYYY-MM-DD`, or relative `-Nh`, `-Nd`, `-Nw`, `-Nm`, `-Ny`. Relative
  starts are aligned to the start of the hour (h) or day (others). A bare `date_to` date
  is inclusive: `2026-06-30` includes all of June 30. Omitting `date_to` means now.
- **Filters.** `{"key", "operator", "value"}`; operators `eq`, `neq` (text comparison;
  `value` may be a list for "any of"), `contains`, `not_contains` (case-insensitive),
  `gt`, `gte`, `lt`, `lte` (numeric; events without the property never match),
  `is_set`, `is_not_set` (no `value`). Filters in a list are ANDed. `neq` matches events
  without the property.
- **Persons.** Events are attributed to persons. Anonymous and identified ids of one
  user are the same person once `$identify` linked them, including events captured
  before the link.
- **Breakdown labels.** `$none` = the property was not set. `$other` = values beyond
  `breakdown_limit`.

## Errors

MCP: the tool result has `isError: true` and a text message. REST: HTTP 400/404 with
`{"error": code, "message": text, "suggestions": [names]}`.

| code | Meaning | What to do |
| --- | --- | --- |
| `unknown_event` | Event name not in the project | Use a name from `suggestions` or `list_events` |
| `unknown_property` | Key never set on the queried events | Use `suggestions` or `get_event_properties` |
| `invalid_query` | Shape or value is wrong (message names the field) | Fix that field |
| `not_found` | Insight or dashboard id not in this project | List first |
| `query_failed` (503) | Database unavailable | Retry later |

## REST endpoints

| Method and path | Body / query | Returns |
| --- | --- | --- |
| `GET /api/projects` | | the key's project |
| `GET /api/projects/{id}/events` | | as `list_events` |
| `GET /api/projects/{id}/properties?event=E` | | as `get_event_properties` |
| `POST /api/projects/{id}/query` | query object | trend, funnel or retention result |
| `GET, POST /api/projects/{id}/insights` | `{"name", "query", "description"?}` | insights |
| `GET, PATCH, DELETE /api/projects/{id}/insights/{iid}` | | insight |
| `GET /api/projects/{id}/insights/{iid}/result` | | result of the stored query |
| `GET, POST /api/projects/{id}/dashboards` | `{"name", "insight_ids"}` | dashboards |
| `GET /api/schema/query` | | JSON Schema of the query object |

## Sending events

```
POST /capture
Content-Type: application/json            (Content-Encoding: gzip is accepted)
{"api_key": "pp_...", "batch": [{"event": "signed_up", "distinct_id": "user-1",
  "properties": {"plan": "free"}, "timestamp": "2026-06-01T10:00:00Z",
  "uuid": "0b7a4f4e-8a39-4c1e-9a76-3f6f1d1b2c3d"}]}
```

Response: `{"status": "ok", "accepted": n, "rejected": [{"index", "error"}]}`. Send the same
`uuid` and `timestamp` when retrying; duplicates are then collapsed. To link an anonymous
id to a user: event `$identify`, `distinct_id` = user id, property `$anon_distinct_id` =
anonymous id. HTTP 503 with `Retry-After` means ingestion is behind: retry later.
