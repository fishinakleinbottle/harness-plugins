#!/usr/bin/env bash
# A small FastAPI service with known gaps: two untested endpoints, one endpoint
# touched only by a ghost test, five ghost tests, one uncollected test file,
# and one module no test reaches. Needs uv to create the environment.
set -euo pipefail
git init -q -b main
git config user.name "Eval"
git config user.email "eval@example.invalid"
mkdir -p app/routers tests
cat > pyproject.toml <<'TOML'
[project]
name = "shop"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["fastapi>=0.109", "httpx>=0.27"]

[dependency-groups]
dev = ["pytest>=8", "pytest-cov>=5"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.coverage.run]
source = ["app"]
branch = true
TOML
: > app/__init__.py
: > app/routers/__init__.py
: > tests/__init__.py
cat > app/main.py <<'PY'
from fastapi import FastAPI, HTTPException

from app.routers import users

app = FastAPI()
app.include_router(users.router)

ITEMS: dict[int, dict] = {1: {"name": "pen", "price": 2.0}}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/items/{item_id}")
def get_item(item_id: int):
    if item_id not in ITEMS:
        raise HTTPException(status_code=404, detail="not found")
    return ITEMS[item_id]


@app.post("/items")
def create_item(name: str, price: float):
    new_id = max(ITEMS) + 1
    ITEMS[new_id] = {"name": name, "price": price}
    return {"id": new_id}


@app.delete("/items/{item_id}")
def delete_item(item_id: int):
    ITEMS.pop(item_id, None)
    return {"deleted": item_id}
PY
cat > app/routers/users.py <<'PY'
from fastapi import APIRouter

router = APIRouter(prefix="/users", tags=["users"])
USERS = {1: "ada", 2: "grace"}


@router.get("")
def list_users():
    return list(USERS.values())


@router.put("/{user_id}")
def rename_user(user_id: int, name: str):
    USERS[user_id] = name
    return {"id": user_id, "name": name}
PY
cat > app/reports.py <<'PY'
def monthly_total(amounts: list[float]) -> float:
    return round(sum(amounts), 2)
PY
cat > tests/test_api.py <<'PY'
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").status_code == 200


def test_get_item():
    assert client.get("/items/1").json()["name"] == "pen"


def test_list_users():
    assert client.get("/users").json() == ["ada", "grace"]


def test_create_item():
    client.post("/items", params={"name": "cup", "price": 3.0})


def test_always_true():
    assert True


def test_todo_delete():
    pass


@pytest.mark.skip(reason="flaky")
def test_delete_item():
    assert client.delete("/items/1").json()["deleted"] == 1


def test_health():  # noqa: F811
    assert client.get("/health").json() == {"ok": True}
PY
cat > tests/helper_checks.py <<'PY'
def test_not_collected():
    assert 1 == 1
PY
cat > CLAUDE.md <<'MD'
# shop

Run tests with `uv run pytest -q`. Coverage config lives in `pyproject.toml`.
MD
printf '__pycache__/\n.pytest_cache/\n.venv/\ncoverage.json\n.coverage\n' > .gitignore
# Prefer a Python the eval sandbox can execute (pyenv shims are blocked there).
for cand in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$cand" ] && "$cand" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
    export UV_PYTHON="$cand"; break
  fi
done
uv sync --quiet
git add -A
git commit -q -m "Initial shop API"
