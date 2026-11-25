# Agentic Text-to-SQL Analytics Assistant

Ask questions about NYC taxi trips in plain English and get back an answer, the SQL that produced it, and the result rows.

A multi-agent workflow built with **LangGraph** and **OpenAI function calling** turns each question into a PostgreSQL query over the NYC TLC trip records (2023 yellow and green taxis, tens of millions of rows). A **reviewer agent** checks every executed query against the question and sends it back for a rewrite when something is off. Queries run as a **read-only database user** behind a SQL guard, so no generated statement can modify data. The whole thing is served through **FastAPI** and runs with **Docker Compose**.

```bash
curl -s localhost:8000/ask -H 'Content-Type: application/json' \
  -d '{"question": "Which 5 pickup zones had the most trips in March 2023?"}'
```

The response contains the plain-English `answer`, the final `sql`, `columns` and `rows`, a `status` (`answered`, `unverified` if the reviewer never approved, or `failed`), the number of `attempts`, any open `review_issues`, total `latency_ms`, and a per-node `trace` with timings.

## How it works

```mermaid
flowchart LR
    Q([Question]) --> L[Schema linker<br/><i>select_tables</i>]
    L --> W[SQL writer<br/><i>submit_sql</i>]
    W --> G{SQL guard}
    G -- blocked --> W
    G -- ok --> E[(Execute as<br/>read-only role)]
    E -- error --> W
    E -- rows --> R[Reviewer<br/><i>review_query</i>]
    R -- revise --> W
    R -- approve --> A[Answer writer]
    A --> O([Answer + SQL + rows])
```

| Node | What it does |
|---|---|
| **Schema linker** | Picks the tables the question needs from a catalog built by introspecting Postgres (table/column comments, approximate row counts, distinct values for low-cardinality columns). |
| **SQL writer** | Generates one PostgreSQL `SELECT` through a forced `submit_sql` function call. On retries it sees every previous attempt with its database error or reviewer notes. |
| **SQL guard** | Parses the query with `sqlglot`: single statement, `SELECT` only, no DML/DDL/`SELECT INTO`/`FOR UPDATE`, no dangerous functions (`pg_sleep`, `pg_read_file`, `dblink`, `set_config`, ...), only known tables. Adds a `LIMIT` when missing. |
| **Executor** | Runs the query on a connection pool as `analyst_ro`, inside a `READ ONLY` transaction with a statement timeout. Database errors are fed back to the writer. |
| **Reviewer** | A second LLM agent sees the question, the SQL and a preview of the result, and returns `approve` or `revise` with concrete issues (wrong join side, missing filter, wrong aggregation, extra columns...). |
| **Answer writer** | Summarises the result in one to three sentences. |

Every structured step is an OpenAI function call with `tool_choice` forced to one function, so the agents always return schema-valid JSON instead of free text.

### Two layers of safety

1. **Static guard** (`sqlagent/safety.py`) rejects anything that is not a single read-only `SELECT` over the allowed tables before it reaches the database.
2. **Database permissions** (`db/init/02_readonly_role.sh`): the agent connects as `analyst_ro`, which has `SELECT` only, `default_transaction_read_only = on` and a 30 s statement timeout. Even a statement that bypassed the guard is refused by Postgres. The integration tests check both layers.

## Quick start

```bash
cp .env.example .env            # add your OPENAI_API_KEY
make install                    # or: pip install -r requirements-dev.txt
make up                         # Postgres 16 with schema + read-only role

# Option A: small synthetic dataset (seconds)
make seed

# Option B: the real 2023 NYC TLC data (downloads ~1 GB of parquet)
make load

make api                        # http://localhost:8000/docs
```

Or run everything in containers: `docker compose up --build`, then load data with `make seed` or `make load` from the host.

From the terminal:

```bash
python -m sqlagent.cli "What percentage of trips in July 2023 were paid by credit card?"
```

### Loading the real data

`scripts/load_tlc_data.py` downloads the monthly parquet files from the TLC CDN, streams them in 500k-row batches, normalises yellow and green schemas into one `trips` table, drops trips whose pickup falls outside the file's month, and bulk-loads with `COPY`. Indexes are dropped during the load and rebuilt afterwards, followed by `ANALYZE`.

```bash
python scripts/load_tlc_data.py --year 2023 --types yellow green      # full year
python scripts/load_tlc_data.py --months 1 2 3 --types yellow         # partial
```

## Data model

| Table | Rows | Notes |
|---|---|---|
| `trips` | one per trip | `taxi_type`, pickup/dropoff times and zones, distance, passengers, fare components, payment type |
| `taxi_zones` | 265 | `location_id`, `borough`, `zone`, `service_zone` |
| `payment_types`, `rate_codes`, `vendors` | small | Lookup tables from the TLC data dictionary |

Column comments in `db/init/01_schema.sql` carry domain knowledge (tips are only recorded for card payments, which column to use for date filters, rate code meanings) and are injected into the prompts automatically.

## Evaluation

`eval/benchmark.jsonl` holds **220 questions** with gold SQL, generated by `eval/build_benchmark.py`:

- 45 hand-written questions covering multi-table joins, double joins on `taxi_zones` (pickup and dropoff), window functions, percentiles, CTEs and conditional aggregation
- 175 templated questions that repeat common analyses across months, boroughs, zones, weekdays, payment types and vendors
- 72 easy, 120 medium, 28 hard

Scoring is **execution match**: the agent's final query and the gold query both run against the same database, and the results must contain the same rows (order-insensitive, numbers compared to 2 decimals, column order ignored, column count must match). See `eval/matching.py`.

```bash
make eval                 # full agent: reviewer on, up to 3 attempts
make eval-baseline        # ablation: single-shot writer, no reviewer
python -m eval.run_eval --difficulty hard --limit 20 --model gpt-4.1
```

Each run writes per-question records (SQL, attempts, errors, reviewer notes, result previews) to `results/<run>.jsonl` and a summary table to `results/<run>_summary.md` with:

- **Execution accuracy**, overall and by difficulty
- **Failed query rate**: share of questions whose final SQL was blocked or errored
- **Median and p95 latency** per question
- Average attempts, token usage and estimated cost

Running both `make eval` and `make eval-baseline` shows how much the reviewer loop adds over single-shot generation.

## Project structure

```
sqlagent/
  agents/graph.py        LangGraph workflow, nodes and routing
  agents/prompts.py      System prompts and prompt builders
  agents/tools.py        Function-calling JSON schemas
  api/main.py            FastAPI app (/ask, /schema, /health)
  safety.py              sqlglot-based SQL guard
  db.py                  Read-only pooled query execution
  schema_catalog.py      Postgres introspection -> prompt context
  llm.py                 OpenAI client with forced function calls + token accounting
  cli.py                 Command-line interface
db/init/                 Schema, read-only role, indexes (run by the Postgres container)
scripts/
  load_tlc_data.py       Real NYC TLC loader (parquet -> COPY)
  seed_sample.py         Synthetic dataset for dev and CI
eval/
  benchmark.jsonl        220 questions with gold SQL
  build_benchmark.py     Benchmark generator
  matching.py            Execution-match comparison
  run_eval.py            Benchmark runner and report
tests/                   Unit tests + integration tests against real Postgres
```

## Testing

```bash
pytest                                                          # unit tests
TEST_DATABASE_URL=postgresql://analyst_ro:analyst_ro@localhost:5432/nyc_taxi pytest   # + integration
```

The unit tests drive the full graph with a scripted LLM (happy path, reviewer rewrite, guard block, database error recovery, attempt exhaustion). The integration tests run against a seeded Postgres and check that the read-only role refuses writes, timeouts fire, every gold query executes, and the agent recovers from a bad first query. GitHub Actions runs lint plus both suites on every push, with Postgres as a service container.

## Configuration

| Variable | Default | |
|---|---|---|
| `OPENAI_API_KEY` | | |
| `OPENAI_MODEL` | `gpt-4.1-mini` | |
| `DATABASE_URL` | `postgresql://analyst_ro:...` | Read-only role |
| `MAX_ATTEMPTS` | `3` | Writer attempts per question |
| `ENABLE_REVIEWER` | `true` | Turn the reviewer agent off for ablations |
| `STATEMENT_TIMEOUT_MS` | `30000` | Per-query timeout |
| `MAX_ROWS` | `1000` | `LIMIT` injected when a query has none |

## Data source

[NYC Taxi & Limousine Commission Trip Record Data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page). The synthetic seed in `scripts/seed_sample.py` only mimics its schema and rough distributions.

## License

MIT
