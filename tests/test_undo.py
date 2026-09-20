from __future__ import annotations

from conftest import make_profile, make_transcript, read_entries, template_entry

from claude_profiles.cli import main
from claude_profiles.staging import new_run, read_manifest, runs, sha256, write_manifest
from claude_profiles.transcripts import find_transcript


def run(*argv: str) -> int:
    return main(list(argv))


def test_undo_removes_what_an_import_created():
    profile = make_profile(entries=[template_entry()])
    path = make_transcript()
    run("import", "--to", str(profile), "--session", path.stem)
    assert any(e["cliSessionId"] == path.stem for e in read_entries(profile))

    assert run("undo", "--last") == 0
    assert not any(e["cliSessionId"] == path.stem for e in read_entries(profile))
    assert path.is_file()


def test_undo_restores_an_entry_that_overwrite_replaced():
    profile = make_profile(entries=[template_entry()])
    path = make_transcript()
    run("import", "--to", str(profile), "--session", path.stem)
    first = next(e for e in read_entries(profile) if e["cliSessionId"] == path.stem)

    run("import", "--to", str(profile), "--overwrite", path.stem)
    run("undo", "--last")

    now = [e for e in read_entries(profile) if e["cliSessionId"] == path.stem]
    assert [e["sessionId"] for e in now] == [first["sessionId"]]


def test_undo_deletes_a_republished_transcript_but_not_the_original():
    profile = make_profile(entries=[template_entry()])
    path = make_transcript(cwd="/old/root", subagents=1)
    run("import", "--to", str(profile), "--session", path.stem, "--remap", "/old/root=/new/root")
    entry = next(e for e in read_entries(profile) if e["cliSessionId"] != "template-cli-id")
    published = find_transcript(entry["cliSessionId"])

    run("undo", "--last")
    assert published is not None and not published.exists()
    assert not (published.parent / published.stem).exists()
    assert path.is_file()


def test_undo_can_reverse_one_record_and_keep_the_rest():
    profile = make_profile(entries=[template_entry()])
    first = make_transcript(title="First")
    second = make_transcript(title="Second")
    run("import", "--to", str(profile), "--limit", "10")

    run("undo", "--last", "--only", first.stem)
    kept = {e["cliSessionId"] for e in read_entries(profile)}
    assert second.stem in kept
    assert first.stem not in kept

    manifest = read_manifest(runs()[0])
    assert len(manifest["records"]) == 1
    assert len(manifest["undone"]) == 1


def test_undo_refuses_an_id_that_is_not_in_the_manifest(capsys):
    profile = make_profile(entries=[template_entry()])
    run("import", "--to", str(profile), "--session", make_transcript().stem)
    assert run("undo", "--last", "--only", "not-a-real-id") == 1
    assert "not in this manifest" in capsys.readouterr().err


def test_undo_lists_recorded_runs(capsys):
    profile = make_profile(entries=[template_entry()])
    run("import", "--to", str(profile), "--session", make_transcript().stem)
    assert run("undo", "--list") == 0
    assert "import" in capsys.readouterr().out


def test_undo_needs_a_target(capsys):
    assert run("undo") == 1
    assert "--last" in capsys.readouterr().err


def test_undo_reports_a_missing_manifest(tmp_path, capsys):
    assert run("undo", str(tmp_path)) == 1
    assert "no manifest.json" in capsys.readouterr().err


def test_undo_list_and_last_report_no_runs(capsys):
    assert run("undo", "--list") == 0
    assert "No runs recorded" in capsys.readouterr().out

    assert run("undo", "--last") == 1
    assert "no runs recorded" in capsys.readouterr().err


def test_undo_reports_gone_indexes_backups_and_changed_originals(tmp_path, capsys):
    original = tmp_path / "orig.jsonl"
    original.write_text("hello\n")
    original_sha = sha256(original)
    original.write_text("changed after the run\n")

    present_backup = tmp_path / "present-backup.json"
    present_backup.write_text("{}")
    replaced_in_place = tmp_path / "replaced-in-place.json"
    replaced_in_place.write_text("{}")

    missing_entry = tmp_path / "missing-entry.json"
    base = {
        "indexEntry": str(missing_entry),
        "publishedTranscript": None,
        "originalTranscript": str(original),
        "originalSha256": original_sha,
    }
    records = [
        {
            **base,
            "title": "No backup recorded",
            "replacedEntryBackup": None,
            "replacedEntryPath": None,
        },
        {
            **base,
            "title": "Backup missing",
            "replacedEntryBackup": str(tmp_path / "no-such-backup.json"),
            "replacedEntryPath": str(tmp_path / "replaced-elsewhere.json"),
        },
        {
            **base,
            "title": "Already restored",
            "replacedEntryBackup": str(present_backup),
            "replacedEntryPath": str(replaced_in_place),
        },
    ]
    run_dir = new_run("import")
    write_manifest(run_dir, "import", tmp_path, records)

    assert main(["undo", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "index already gone" in out
    assert "WARN backup missing" in out
    assert "kept existing" in out
    assert "WARN original changed" in out


def test_undo_migrate_removes_a_copied_file_or_notes_it_is_gone(tmp_path, capsys):
    copied_file = tmp_path / "copied.json"
    copied_file.write_text("{}")
    run_dir = new_run("migrate")
    records = [
        {"title": "File", "path": str(copied_file)},
        {"title": "Gone", "path": str(tmp_path / "already-gone.json")},
    ]
    write_manifest(run_dir, "migrate", tmp_path, records)

    assert main(["undo", str(run_dir)]) == 0
    assert not copied_file.exists()
    out = capsys.readouterr().out
    assert "removed copied file" in out
    assert "already gone" in out


def test_undo_refuses_a_manifest_it_cannot_act_on(tmp_path, capsys):
    empty = new_run("import")
    write_manifest(empty, "import", tmp_path, [])
    assert main(["undo", str(empty)]) == 1
    assert "lists no records" in capsys.readouterr().err

    odd = new_run("mystery")
    write_manifest(odd, "mystery", tmp_path, [{"title": "x"}])
    assert main(["undo", str(odd)]) == 1
    assert "unknown run kind" in capsys.readouterr().err
