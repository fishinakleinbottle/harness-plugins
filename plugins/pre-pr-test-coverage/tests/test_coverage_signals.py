"""Tests for coverage_signals.py.

Each test builds a small repo in tmp_path with a hand-written coverage.json,
so the tests don't need pytest-cov or any web framework installed.
"""

import json
import sys
import textwrap
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "pre-pr-test-coverage"
sys.path.insert(0, str(SKILL_DIR))

import coverage_signals as cs  # noqa: E402


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"))


def file_entry(executed, missing, branches=(0, 0)):
    num_branches, missing_branches = branches
    statements = len(executed) + len(missing)
    pct = 100.0 * len(executed) / statements if statements else 100.0
    return {
        "executed_lines": sorted(executed),
        "missing_lines": sorted(missing),
        "excluded_lines": [],
        "summary": {
            "covered_lines": len(executed),
            "num_statements": statements,
            "percent_covered": pct,
            "missing_lines": len(missing),
            "excluded_lines": 0,
            "num_branches": num_branches,
            "covered_branches": num_branches - missing_branches,
            "missing_branches": missing_branches,
        },
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write(
        tmp_path / "pyproject.toml",
        """
        [project]
        name = "shop"

        [tool.coverage.run]
        source = ["app"]
        """,
    )
    write(tmp_path / "app" / "__init__.py", "")
    write(
        tmp_path / "app" / "main.py",
        """
        from fastapi import FastAPI, HTTPException

        from app.routers import users

        app = FastAPI()
        app.include_router(users.router)


        @app.get("/health")
        def health():
            return {"ok": True}


        @app.post("/items")
        def create_item(name: str):
            return {"name": name}


        @app.delete("/items/{item_id}")
        async def delete_item(item_id: int):
            return {"deleted": item_id}
        """,
    )
    write(tmp_path / "app" / "routers" / "__init__.py", "")
    write(
        tmp_path / "app" / "routers" / "users.py",
        """
        from fastapi import APIRouter

        router = APIRouter(prefix="/users", tags=["users"])


        @router.get("")
        def list_users():
            return []


        @router.put("/{user_id}")
        def rename_user(user_id: int, name: str):
            return {"id": user_id}
        """,
    )
    write(
        tmp_path / "app" / "services.py",
        """
        def price_with_tax(price, rate=0.1):
            if price < 0:
                raise ValueError("negative")
            return round(price * (1 + rate), 2)
        """,
    )
    write(
        tmp_path / "app" / "reports.py",
        """
        def monthly_total(amounts):
            return sum(amounts)
        """,
    )
    write(
        tmp_path / "tests" / "test_api.py",
        """
        import pytest
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)


        def test_health():
            assert client.get("/health").status_code == 200


        def test_list_users():
            assert client.get("/users").json() == []


        def test_create_item():
            client.post("/items", params={"name": "cup"})


        def test_always_true():
            assert True


        def test_todo_delete():
            pass


        @pytest.mark.skip(reason="flaky")
        def test_delete_item():
            assert client.delete("/items/1").json()["deleted"] == 1


        def test_health():
            assert client.get("/health").json() == {"ok": True}


        class TestRaises:
            def test_negative_price(self):
                from app.services import price_with_tax
                with pytest.raises(ValueError):
                    price_with_tax(-1)
        """,
    )
    write(
        tmp_path / "tests" / "helper_checks.py",
        """
        def test_not_collected():
            assert 1 == 1
        """,
    )
    coverage = {
        "meta": {"version": "7.6.0", "branch_coverage": True},
        "files": {
            "app/__init__.py": file_entry([], []),
            # def lines: health 9/10, create_item 14/15, delete_item 19/20
            "app/main.py": file_entry([1, 3, 5, 6, 9, 10, 11, 14, 15, 16, 19, 20], [21]),
            "app/routers/__init__.py": file_entry([], []),
            "app/routers/users.py": file_entry([1, 3, 6, 7, 8, 11, 12], [13]),
            "app/services.py": file_entry([1, 2, 3, 4], [], branches=(2, 0)),
            "app/reports.py": file_entry([], [1, 2]),
        },
        "totals": {
            "covered_lines": 23,
            "num_statements": 27,
            "percent_covered": 85.0,
            "missing_lines": 4,
            "num_branches": 2,
            "covered_branches": 2,
            "missing_branches": 0,
        },
    }
    (tmp_path / "coverage.json").write_text(json.dumps(coverage))
    return tmp_path


def run(repo: Path, **kwargs):
    return cs.analyze(
        repo=repo,
        coverage_path=repo / "coverage.json",
        **kwargs,
    )


def test_endpoints_are_discovered_with_router_prefix(repo):
    result = run(repo)
    found = {(e["method"], e["path"]): e for e in result["endpoints"]}
    assert set(found) == {
        ("GET", "/health"),
        ("POST", "/items"),
        ("DELETE", "/items/{item_id}"),
        ("GET", "/users"),
        ("PUT", "/users/{user_id}"),
    }
    assert found[("GET", "/health")]["status"] == "tested"
    assert found[("GET", "/users")]["status"] == "tested"
    # Executed, but the only test naming the path is a ghost test.
    assert found[("POST", "/items")]["status"] == "weak"
    assert found[("POST", "/items")]["referenced_by"] == ["tests/test_api.py::test_create_item"]
    # Handler body never ran and no test references it.
    assert found[("DELETE", "/items/{item_id}")]["status"] == "untested"
    assert found[("PUT", "/users/{user_id}")]["status"] == "untested"
    assert found[("PUT", "/users/{user_id}")]["file"] == "app/routers/users.py"
    assert found[("DELETE", "/items/{item_id}")]["handler"] == "delete_item"


def test_ghost_tests_are_flagged_with_reasons(repo):
    result = run(repo)
    ghosts = {(g["name"], g["reason"]) for g in result["ghost_tests"]}
    assert ("test_create_item", "no assertion") in ghosts
    assert ("test_always_true", "trivial assertion") in ghosts
    assert ("test_todo_delete", "empty body") in ghosts
    assert ("test_delete_item", "skipped") in ghosts
    assert any(n == "test_health" and r.startswith("duplicate name") for n, r in ghosts)
    # A pytest.raises block counts as an assertion.
    assert not any(n == "test_negative_price" for n, _ in ghosts)
    # Real tests are not flagged.
    assert not any(n == "test_list_users" for n, _ in ghosts)


def test_uncollected_test_files_and_untested_modules(repo):
    result = run(repo)
    assert result["uncollected_test_files"] == ["tests/helper_checks.py"]
    untested = {m["file"] for m in result["untested_modules"]}
    assert untested == {"app/reports.py"}


def test_file_deltas_against_baseline(repo):
    baseline = {
        "overall": {"percent_covered": 90.0},
        "files": {"app/services.py": 100.0, "app/main.py": 95.0, "app/routers/users.py": 87.5},
        "untested_endpoints": ["PUT /users/{user_id}"],
        "ghost_tests": ["tests/test_api.py::test_create_item"],
    }
    (repo / "baseline.json").write_text(json.dumps(baseline))
    result = run(repo, baseline_path=repo / "baseline.json")
    files = {f["file"]: f for f in result["files"]}
    assert files["app/main.py"]["status"] == "regressed"
    assert files["app/main.py"]["delta"] == pytest.approx(92.3 - 95.0, abs=0.1)
    assert files["app/reports.py"]["status"] == "new"
    assert files["app/services.py"]["status"] == "steady"
    assert result["summary"]["overall_delta"] == pytest.approx(85.0 - 90.0, abs=0.01)
    new_untested = {e for e in result["summary"]["new_untested_endpoints"]}
    assert new_untested == {"DELETE /items/{item_id}"}
    assert "tests/test_api.py::test_always_true" in result["summary"]["new_ghost_tests"]
    assert "tests/test_api.py::test_create_item" not in result["summary"]["new_ghost_tests"]


def test_gates_without_baseline_are_informational_where_they_need_one(repo):
    result = run(repo, pytest_exit_code=0)
    gates = {g["name"]: g for g in result["gates"]}
    assert gates["tests-green"]["status"] == "pass"
    assert gates["overall-regression"]["status"] == "skipped"
    assert gates["new-file-floor"]["status"] == "fail"  # reports.py at 0%
    assert gates["uncollected-test-files"]["status"] == "fail"
    assert gates["untested-endpoints"]["status"] == "info"
    assert result["summary"]["verdict"] == "fail"


def test_gates_with_baseline_and_floor(repo):
    baseline = {
        "overall": {"percent_covered": 85.2},
        "files": {"app/main.py": 92.3, "app/routers/users.py": 87.5, "app/services.py": 100.0, "app/reports.py": 0.0},
        "untested_endpoints": ["DELETE /items/{item_id}", "PUT /users/{user_id}"],
        "ghost_tests": [],
    }
    (repo / "baseline.json").write_text(json.dumps(baseline))
    result = run(
        repo,
        baseline_path=repo / "baseline.json",
        pytest_exit_code=1,
        config={"floor": 95, "max_overall_regression": 1.0, "max_file_regression": 2.0},
    )
    gates = {g["name"]: g for g in result["gates"]}
    assert gates["tests-green"]["status"] == "fail"
    assert gates["overall-floor"]["status"] == "fail"
    assert gates["overall-regression"]["status"] == "pass"  # 85.0 vs 85.2 is within 1.0
    assert gates["file-regression"]["status"] == "pass"
    assert gates["untested-endpoints"]["status"] == "pass"  # none new
    assert gates["ghost-tests"]["status"] == "fail"  # new ghosts vs empty baseline


def test_config_is_read_from_pyproject(repo):
    write(
        repo / "pyproject.toml",
        """
        [tool.coverage.run]
        source = ["app"]

        [tool.pre-pr-test-coverage]
        floor = 70
        tests = ["tests"]
        baseline = ".ai/coverage/baseline.json"
        """,
    )
    cfg = cs.load_config(repo)
    assert cfg["floor"] == 70
    assert cfg["baseline"] == ".ai/coverage/baseline.json"
    assert cfg["source"] == ["app"]


def test_write_baseline_round_trips(repo):
    result = run(repo, pytest_exit_code=0)
    out = repo / ".ai" / "coverage" / "baseline.json"
    cs.write_baseline(result, out)
    data = json.loads(out.read_text())
    assert data["files"]["app/main.py"] == pytest.approx(92.3, abs=0.1)
    assert "DELETE /items/{item_id}" in data["untested_endpoints"]
    assert "tests/test_api.py::test_todo_delete" in data["ghost_tests"]
    assert data["overall"]["percent_covered"] == pytest.approx(85.0)
    # Feeding it straight back yields a steady, no-new-findings run.
    again = run(repo, baseline_path=out, pytest_exit_code=0)
    assert again["summary"]["new_untested_endpoints"] == []
    assert again["summary"]["new_ghost_tests"] == []


def test_markdown_report_has_every_section(repo):
    result = run(repo, pytest_exit_code=0)
    md = cs.render_markdown(result)
    for heading in ("## Gates", "## Coverage by file", "## Endpoints", "## Ghost tests", "## Modules without tests"):
        assert heading in md
    assert "DELETE /items/{item_id}" in md
    assert "test_todo_delete" in md


def test_flask_and_django_routes(tmp_path):
    write(tmp_path / "pyproject.toml", "[tool.coverage.run]\nsource = ['web']\n")
    write(tmp_path / "web" / "__init__.py", "")
    write(
        tmp_path / "web" / "views.py",
        """
        from flask import Blueprint, Flask

        app = Flask(__name__)
        bp = Blueprint("admin", __name__, url_prefix="/admin")


        @app.route("/ping")
        def ping():
            return "pong"


        @bp.route("/users", methods=["GET", "POST"])
        def users():
            return "users"


        @app.get("/items")
        def items():
            return "items"
        """,
    )
    write(
        tmp_path / "web" / "urls.py",
        """
        from django.urls import path

        from . import views

        urlpatterns = [
            path("orders/", views.orders, name="orders"),
            path("orders/<int:pk>/", views.order_detail),
        ]
        """,
    )
    write(tmp_path / "tests" / "test_web.py", "def test_ping(client):\n    assert client.get('/ping').status_code == 200\n")
    coverage = {
        "files": {
            "web/views.py": file_entry([1, 3, 4, 7, 8, 9, 12, 13, 17, 18], [14, 19]),
            "web/urls.py": file_entry([1, 3, 5, 6, 7], []),
        },
        "totals": {"percent_covered": 80.0, "num_statements": 15, "covered_lines": 12, "missing_lines": 3,
                   "num_branches": 0, "covered_branches": 0, "missing_branches": 0},
    }
    (tmp_path / "coverage.json").write_text(json.dumps(coverage))
    result = cs.analyze(repo=tmp_path, coverage_path=tmp_path / "coverage.json")
    found = {(e["method"], e["path"]): e["status"] for e in result["endpoints"]}
    assert found[("GET", "/ping")] == "tested"
    assert found[("GET", "/admin/users")] == "untested"
    assert found[("POST", "/admin/users")] == "untested"
    assert found[("GET", "/items")] == "untested"
    assert ("ANY", "/orders/") in found
    assert ("ANY", "/orders/<int:pk>/") in found
