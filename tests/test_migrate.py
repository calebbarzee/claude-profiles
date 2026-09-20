from __future__ import annotations

import json
from pathlib import Path

from conftest import ACCOUNT, ORG, make_profile, make_transcript, read_entries, template_entry

from claude_profiles.cli import main
from claude_profiles.commands import migrate
from claude_profiles.paths import default_profile


def run(*argv: str) -> int:
    return main(list(argv))


def source_profile() -> object:
    profile = make_profile("source", entries=[template_entry(sessionId="local_a")])
    shadow = profile / "git-shadow" / "repo-1"
    shadow.mkdir(parents=True)
    (shadow / "HEAD").write_text("ref: refs/heads/main\n")
    return profile


def test_migrate_copies_the_index_and_shadow_repos(capsys):
    source = source_profile()
    target = make_profile("target", entries=[template_entry(sessionId="local_z")])
    assert run("migrate", "--from", str(source), "--to", str(target), "--yes") == 0

    assert {e["sessionId"] for e in read_entries(target)} == {"local_a", "local_z"}
    assert (target / "git-shadow" / "repo-1" / "HEAD").is_file()
    assert "local_a.json" in capsys.readouterr().out


def test_migrate_remaps_across_accounts():
    source = source_profile()
    target = make_profile("target")
    other = target / "claude-code-sessions" / "other-acct" / "other-org"
    other.mkdir(parents=True)
    (target / "claude-code-sessions" / ACCOUNT).rename(target / "claude-code-sessions" / "gone")
    (target / "claude-code-sessions" / "gone").rename(target / "claude-code-sessions" / "tmp")
    import shutil

    shutil.rmtree(target / "claude-code-sessions" / "tmp")

    assert run("migrate", "--from", str(source), "--to", str(target), "--yes") == 0
    assert (other / "local_a.json").is_file()


def test_migrate_backs_up_what_it_overwrites_and_undo_restores_it():
    source = source_profile()
    target = make_profile("target", entries=[template_entry(sessionId="local_a", title="Mine")])
    run("migrate", "--from", str(source), "--to", str(target), "--yes")
    assert read_entries(target)[0]["title"] == "Template session"

    run("undo", "--last")
    assert read_entries(target)[0]["title"] == "Mine"
    assert not (target / "git-shadow" / "repo-1").exists()


def test_migrate_dry_run_writes_nothing(capsys):
    source = source_profile()
    target = make_profile("target")
    assert run("migrate", "--from", str(source), "--to", str(target), "--dry-run") == 0
    assert not read_entries(target)
    assert "nothing written" in capsys.readouterr().out


def test_migrate_defaults_to_the_default_profile():
    scope = default_profile() / "claude-code-sessions" / ACCOUNT / ORG
    scope.mkdir(parents=True)
    import json

    (scope / "local_a.json").write_text(json.dumps(template_entry(sessionId="local_a")))
    target = make_profile("target")
    assert run("migrate", "--to", str(target), "--yes") == 0
    assert read_entries(target)[0]["sessionId"] == "local_a"


def test_migrate_refuses_a_profile_onto_itself(capsys):
    source = source_profile()
    assert run("migrate", "--from", str(source), "--to", str(source), "--yes") == 1
    assert "same directory" in capsys.readouterr().err


def test_migrate_reports_when_the_source_has_nothing(capsys):
    source = make_profile("source")
    target = make_profile("target")
    assert run("migrate", "--from", str(source), "--to", str(target), "--yes") == 1
    assert "no local_*.json" in capsys.readouterr().err


def test_migrate_warns_when_a_transcript_is_missing(capsys):
    source = source_profile()
    target = make_profile("target")
    run("migrate", "--from", str(source), "--to", str(target), "--yes")
    assert "missing from" in capsys.readouterr().out


def test_migrate_reports_ok_when_the_transcript_is_present(capsys):
    path = make_transcript()
    source = make_profile("source", entries=[template_entry(cliSessionId=path.stem)])
    target = make_profile("target")
    run("migrate", "--from", str(source), "--to", str(target), "--yes")
    out = capsys.readouterr().out
    assert "missing from" not in out


def test_migrate_aborts_when_a_profile_is_missing_or_running(monkeypatch, tmp_path, capsys):
    target = make_profile("target")
    assert run("migrate", "--from", str(tmp_path / "missing"), "--to", str(target), "--yes") == 1
    assert "profile not found" in capsys.readouterr().err

    source = source_profile()
    monkeypatch.setattr("claude_profiles.commands.migrate.profile_is_running", lambda _: True)
    assert run("migrate", "--from", str(source), "--to", str(target), "--yes") == 1
    assert "quit it first" in capsys.readouterr().err


def test_migrate_confirmation_prompt(monkeypatch, capsys):
    source = source_profile()
    target = make_profile("target")

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert run("migrate", "--from", str(source), "--to", str(target)) == 1
    assert "not a terminal" in capsys.readouterr().err

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _="": "n")
    assert run("migrate", "--from", str(source), "--to", str(target)) == 0
    assert "Aborted." in capsys.readouterr().out


def test_migrate_leaves_an_existing_shadow_repo_alone(capsys):
    source = source_profile()
    target = make_profile("target")
    existing = target / "git-shadow" / "repo-1"
    existing.mkdir(parents=True)
    (existing / "marker").write_text("keep")

    assert run("migrate", "--from", str(source), "--to", str(target), "--yes") == 0
    assert (existing / "marker").is_file()
    assert "already present, left alone" in capsys.readouterr().out


def test_validate_reports_every_way_a_copy_can_fail(tmp_path, capsys):
    dst_scope = tmp_path / "dst"
    dst_scope.mkdir()
    (dst_scope / "local_bad.json").write_text("{not json")
    (dst_scope / "local_ok.json").write_text(json.dumps({"title": "x"}))
    files = [Path("local_missing.json"), Path("local_bad.json"), Path("local_ok.json")]

    ok = migrate._validate(files, dst_scope)
    out = capsys.readouterr().out
    assert ok is False
    assert "not copied" in out
    assert "not valid JSON" in out
    assert "has no cliSessionId" in out
