# Testing Guide — Nightreign Relic Planner

## Overview

Three independent test suites, each runnable without the others:

| Suite | Framework | Coverage |
|-------|-----------|---------|
| **nrplanner/** | pytest | Pure Python package: game data loading, relic validation, scoring, optimization |
| **backend/** | pytest + TestClient | FastAPI routes: upload, saves, builds, optimizer, game data |
| **frontend/** | Vitest + RTL | React components/hooks: upload page, local builds hook |

End-to-end tests (Playwright) are in `frontend/tests/` and require the full stack running.

---

## Prerequisites

```bash
# Python environment (uv workspace)
uv sync --all-groups

# Database (required for backend tests)
docker compose up -d
uv run alembic -c backend/alembic.ini upgrade head

# Frontend (required for frontend unit tests)
cd frontend && bun install
```

---

## Running Tests

### nrplanner unit tests

```bash
# From repo root
uv run pytest nrplanner/tests/ -v
```

These tests use the **real** bundled resource files (CSVs, XMLs, JSONs). They do
not need a database or running backend. The `test_constructs_without_error` test
specifically catches the `Json vs json` filesystem casing regression.

### Backend unit tests

```bash
# Must run from backend/ so uv uses the backend workspace member environment
cd backend
uv run pytest tests/ -v -m "not integration"
```

Requires a running PostgreSQL (from `docker compose up -d`). The binary
save-parsing layer (`decrypt_sl2`, `discover_characters`, `parse_relics`) is
**mocked** in unit tests — only the FastAPI routing and DB logic run for real.

### Backend integration test (real save file)

The integration test uploads a real `.sl2` save file through the full
nrplanner parsing stack:

```bash
# 1. Copy your save file to the fixtures directory
cp "C:\Users\aliig\AppData\Roaming\Nightreign\76561198039949473\NR0000.sl2" \
   backend/tests/fixtures/NR0000.sl2

# 2. Run integration tests (from backend/)
cd backend
uv run pytest tests/ -m integration -v
```

The test is skipped automatically if the fixture file is absent, so CI passes
without it.

### All Python tests (excluding integration)

```bash
# nrplanner from repo root; backend from backend/
uv run pytest nrplanner/tests/ -v -m "not integration"
cd backend && uv run pytest tests/ -v -m "not integration"
```

### Frontend unit tests

```bash
cd frontend

# Run once (CI mode)
bun run test:unit

# Watch mode (development)
bun run test:unit:watch

# With coverage report
bun run test:unit:coverage
```

No backend required. Tests run in jsdom (browser-like environment).

### Frontend E2E tests (Playwright)

```bash
cd frontend
bun run test        # headless
bun run test:ui     # interactive UI
```

Requires full stack: `docker compose up` + `bun dev` + backend running at
`localhost:8000`.

---

## Coverage

```bash
# Backend coverage (run from backend/)
cd backend
uv run coverage run -m pytest tests/ -m "not integration"
uv run coverage report
uv run coverage html   # opens htmlcov/index.html

# Frontend coverage
cd frontend && bun run test:unit:coverage
# Opens frontend/coverage/index.html
```

---

## What Is Mocked vs. Real

### Backend tests

| Component | Unit tests | Integration tests |
|-----------|-----------|-------------------|
| `SourceDataHandler` | **Real** (real resource files) | **Real** |
| `get_items_json()` | **Real** (lru_cache, real file) | **Real** |
| `decrypt_sl2` / `split_memory_dat` | **Mocked** | **Real** |
| `discover_characters` | **Mocked** | **Real** |
| `parse_relics` | **Mocked** | **Real** |
| PostgreSQL | **Real** (test DB from .env) | **Real** |

### Frontend tests

| Dependency | Behavior |
|-----------|---------|
| `SavesService.uploadSave` | Mocked via `vi.mock('@/client')` |
| `BuildsService.*` | Mocked via `vi.mock('@/client')` |
| `useMutation` / `useQueryClient` | Mocked per-test via `vi.mock` |
| `useNavigate` | Mocked, returns `vi.fn()` |
| `localStorage` | Real jsdom implementation |

---

## Adding New Tests

### New nrplanner test

1. Create `nrplanner/tests/test_<module>.py`
2. Use the session-scoped fixtures from `conftest.py`:
   - `ds` — shared `SourceDataHandler`
   - `safe_relic_ids` — list of valid relic IDs
   - `all_effects` — list of effect dicts

### New backend route test

1. Create `backend/tests/api/routes/test_<route>.py`
2. Import `client`, `superuser_token_headers`, and `normal_user_token_headers` fixtures
3. For game-data routes, add `@pytest.mark.usefixtures("override_game_data")`
4. `GameDataDep` is overridden to use a session-scoped `SourceDataHandler`

### New frontend component test

1. Create `src/**/__tests__/*.test.tsx` (picked up by vitest.config.ts)
2. Use `renderWithProviders` from `@/test/test-utils` for React Query context
3. Mock heavy dependencies at the top of the file with `vi.mock(...)`

---

## CI Configuration

Add these commands to your CI pipeline:

```yaml
# Python tests (nrplanner from root; backend from backend/)
- run: uv sync --all-groups
- run: uv run pytest nrplanner/tests/ -v -m "not integration"
- run: cd backend && uv run pytest tests/ -v -m "not integration"

# Frontend unit tests
- run: cd frontend && bun install && bun run test:unit
```

Integration tests are excluded by default (`-m "not integration"`). To run them in
CI, add the `NR0000.sl2` fixture and run:
```bash
uv run pytest backend/tests/ -m integration
```
