from __future__ import annotations

import pytest

from claude_profiles import Abort, paths
from claude_profiles.paths import Profile


def test_slugify_collapses_punctuation():
    assert paths.slugify("  Client A / B  ") == "client-a-b"
    assert paths.slugify("!!!") == "profile"


def test_unique_slug_avoids_collisions():
    existing = [Profile("work", "Work", "/tmp/work", 0)]
    assert paths.unique_slug("Work", existing) == "work-2"


def test_registry_round_trip():
    rows = [Profile("work", "Work", "/data/work", 1700000000000)]
    paths.save_registry(rows)
    assert paths.load_registry() == rows


def test_a_missing_registry_is_empty():
    assert paths.load_registry() == []


def test_a_corrupt_registry_aborts_and_is_left_alone():
    paths.registry_path().parent.mkdir(parents=True, exist_ok=True)
    paths.registry_path().write_text("{not json")
    with pytest.raises(Abort, match="not valid JSON"):
        paths.load_registry()
    assert paths.registry_path().read_text() == "{not json"


def test_a_row_missing_a_field_aborts_instead_of_vanishing_on_save():
    paths.registry_path().parent.mkdir(parents=True, exist_ok=True)
    paths.registry_path().write_text(
        '{"version": 1, "profiles": [{"id": "a"}, '
        '{"id": "b", "name": "B", "dataDir": "/d", "createdAt": 5}]}'
    )
    with pytest.raises(Abort, match="row 1 lacks"):
        paths.load_registry()


def test_resolve_profile_accepts_an_id_a_name_or_a_path(tmp_path):
    paths.save_registry([Profile("work", "Work", "/data/work", 0)])
    assert paths.resolve_profile("work") == paths.Path("/data/work")
    assert paths.resolve_profile("Work") == paths.Path("/data/work")
    assert paths.resolve_profile(str(tmp_path)) == tmp_path.resolve()


def test_profile_roots_lists_the_default_and_every_created_profile(home):
    paths.default_profile().mkdir(parents=True)
    (paths.profiles_root() / "one").mkdir(parents=True)
    (paths.profiles_root() / "two").mkdir(parents=True)
    assert paths.profile_roots() == [
        paths.default_profile(),
        paths.profiles_root() / "one",
        paths.profiles_root() / "two",
    ]


def test_state_dir_lives_under_home(home):
    assert home in paths.state_dir().parents


def test_linux_locations_honour_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "is_macos", lambda: False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert paths.default_profile() == tmp_path / "cfg" / "Claude"
    assert paths.profiles_root() == tmp_path / "cfg" / "claude-profiles"
