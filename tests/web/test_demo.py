"""`hub demo` (CH-192): invented data, own folder, every page renders, nothing real touched."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from a11y import check_page
from fastapi.testclient import TestClient

from careerhub.core.models import Job
from careerhub.core.paths import DATA_DIR_ENV, default_data_dir
from careerhub.demo import (
    LABEL,
    MARKER,
    FileKeyStore,
    build_demo,
    check_folder,
    demo_folder,
    reset_folder,
)
from careerhub.web.app import create_app, database_opens, make_session_opener

# 8 p.m. in New York, when UTC is already the next day (bug CH-182).
NOW = datetime(2026, 10, 24, 0, 30, tzinfo=UTC)
PAGES = ["/today", "/plan", "/tracker", "/contacts", "/interactions", "/status", "/conversation"]


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    folder = build_demo(tmp_path / "CareerHubDemo", NOW)
    monkeypatch.setenv(DATA_DIR_ENV, str(folder))
    keys = FileKeyStore(folder)
    app = create_app(
        "t", 8765, lambda: database_opens(keys), make_session_opener(keys), keys, lambda: False,
        banner=LABEL,
    )  # fmt: skip
    client = TestClient(app, base_url="http://127.0.0.1:8765", follow_redirects=False)
    client.get("/?t=t")
    return client


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_labeled_and_accessible(demo: TestClient, path: str) -> None:
    page = demo.get(path)
    assert page.status_code == 200 and LABEL in page.text
    assert check_page(page.text) == []


def test_job_detail_and_the_cards_have_demo_content(demo: TestClient) -> None:
    today = demo.get("/today").text
    assert "Acme Robotics" in today and today.count('class="card job"') >= 5
    assert demo.get("/jobs/1").status_code == 200
    assert "Cobalt Cloud" in demo.get("/tracker").text


def test_the_demo_has_about_thirty_jobs_and_uses_its_own_database(
    demo: TestClient, tmp_path: Path
) -> None:
    keys = FileKeyStore(tmp_path / "CareerHubDemo")
    with make_session_opener(keys)() as session:
        assert session.query(Job).count() == 30
    assert (tmp_path / "CareerHubDemo" / MARKER).exists()
    assert not default_data_dir().exists()  # the real folder was not created


def test_the_demo_refuses_the_real_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # macOS: the real folder lives under HOME
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))  # Linux
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    real = default_data_dir()
    assert demo_folder() != real
    for folder in (real, real / "inside"):
        with pytest.raises(ValueError, match="real Career Hub folder"):
            check_folder(folder)
        with pytest.raises(ValueError, match="real Career Hub folder"):
            build_demo(folder, NOW)
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "elsewhere"))
    with pytest.raises(ValueError, match="real Career Hub folder"):
        check_folder(tmp_path / "elsewhere")


def test_reset_removes_only_a_marked_demo_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    other = tmp_path / "Documents"
    other.mkdir()
    (other / "keep.txt").write_text("mine")
    with pytest.raises(ValueError, match="not made by"):
        reset_folder(other)
    assert (other / "keep.txt").exists()
    folder = build_demo(tmp_path / "CareerHubDemo", NOW)
    reset_folder(folder)
    assert not folder.exists()


def test_reset_never_deletes_a_real_folder_inside_the_demo_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding: a real folder nested in the demo folder must stop --reset."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    folder = build_demo(tmp_path / "CareerHubDemo", NOW)
    mine = folder / "mine"
    mine.mkdir()
    (mine / "careerhub.db").write_text("real")
    monkeypatch.setenv(DATA_DIR_ENV, str(mine))
    with pytest.raises(ValueError, match="real Career Hub folder"):
        reset_folder(folder)
    assert (mine / "careerhub.db").exists()


def test_a_folder_that_is_not_a_demo_is_never_built_into(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    other = tmp_path / "CareerHubDemo"
    (other / "profile").mkdir(parents=True)
    (other / "profile" / "profile.md").write_text("mine")
    with pytest.raises(ValueError, match="not made by"):
        build_demo(other, NOW)
    assert (other / "profile" / "profile.md").read_text() == "mine"


def test_a_failed_build_leaves_nothing_that_looks_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import careerhub.demo as demo

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(demo, "_seed_jobs", boom)
    with pytest.raises(OSError):
        build_demo(tmp_path / "CareerHubDemo", NOW)
    assert not (tmp_path / "CareerHubDemo").exists()
    import os

    assert DATA_DIR_ENV not in os.environ
