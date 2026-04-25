"""Detector unit tests — pure functions, no I/O, no LLM.

Each test gives the detector a fake file tree + manifest dict and asserts
the top candidate. We test the *contract* — top framework + minimum
confidence — not the exact score, so tweaking heuristic weights doesn't
break the suite.
"""
from __future__ import annotations

from shared.analysis.detector import detect
from shared.analysis.schemas import Framework


def _top(paths: list[str], manifests: dict[str, str]) -> Framework:
    return detect(paths, manifests)[0].framework


def test_detects_nextjs_from_config_file():
    assert _top(
        ["package.json", "next.config.js", "pages/index.tsx"],
        {"package.json": '{"dependencies": {"next": "14.0.0", "react": "18.0.0"}}'},
    ) == Framework.NEXT_JS


def test_detects_nextjs_from_dependency_only():
    assert _top(
        ["package.json", "src/app/page.tsx"],
        {"package.json": '{"dependencies": {"next": "14.0.0", "react": "18.0.0"}}'},
    ) == Framework.NEXT_JS


def test_detects_nestjs_before_express():
    # @nestjs/core depends on express transitively — NestJS rule must win.
    assert _top(
        ["package.json", "nest-cli.json", "src/main.ts"],
        {"package.json": '{"dependencies": {"@nestjs/core": "10.0.0", "express": "4.18.0"}}'},
    ) == Framework.NEST_JS


def test_detects_vite_react():
    assert _top(
        ["package.json", "vite.config.ts", "src/main.tsx"],
        {"package.json": '{"dependencies": {"react": "18.0.0", "vite": "5.0.0"}}'},
    ) == Framework.REACT_VITE


def test_detects_express_plain():
    assert _top(
        ["package.json", "server.js"],
        {"package.json": '{"dependencies": {"express": "4.18.0"}}'},
    ) == Framework.EXPRESS


def test_detects_django_from_managepy():
    # manage.py wins over a generic pyproject without django listed
    assert _top(
        ["manage.py", "myapp/settings.py", "requirements.txt"],
        {"requirements.txt": "django>=5.0\npsycopg[binary]>=3.1"},
    ) == Framework.DJANGO


def test_detects_fastapi_from_pyproject():
    assert _top(
        ["pyproject.toml", "app/main.py"],
        {"pyproject.toml": 'dependencies = ["fastapi>=0.110", "uvicorn[standard]>=0.29"]'},
    ) == Framework.FASTAPI


def test_detects_flask_from_requirements():
    assert _top(
        ["requirements.txt", "app.py"],
        {"requirements.txt": "Flask==3.0.0\ngunicorn==21.0\n"},
    ) == Framework.FLASK


def test_detects_go():
    assert _top(["go.mod", "main.go"], {"go.mod": "module example.com/app"}) == Framework.GO_NET_HTTP


def test_detects_rust():
    assert _top(["Cargo.toml", "src/main.rs"], {"Cargo.toml": "[package]\nname = 'x'"}) == Framework.RUST_AXUM


def test_static_html_fallback():
    assert _top(["index.html", "style.css"], {}) == Framework.STATIC_HTML


def test_unknown_when_no_signal():
    cands = detect(["LICENSE", "README.md"], {})
    assert cands[0].framework == Framework.UNKNOWN
    assert cands[0].confidence == 0.0


def test_confidence_caps_at_one():
    cands = detect(
        ["package.json", "next.config.js"],
        {"package.json": '{"dependencies": {"next": "14"}}'},
    )
    assert 0.0 < cands[0].confidence <= 1.0
