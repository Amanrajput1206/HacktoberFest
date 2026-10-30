from handoff.miner import is_source, merge_identities, mine_repo
from tests.conftest import body


def test_rename_keeps_history(repo):
    repo.commit("Alice", {"old.py": body(0)})
    repo.commit("Alice", rename=("old.py", "pkg/new.py"))
    repo.commit("Alice", {"pkg/new.py": body(1)})
    h = mine_repo(repo.path)
    assert h.head_files == ["pkg/new.py"]
    assert [c.files for c in h.commits] == [["pkg/new.py"]] * 3
    assert h.commits[0].added == ["pkg/new.py"]


def test_deleted_files_are_ignored(repo):
    repo.commit("Alice", {"a.py": body(0), "gone.py": body(0)})
    repo.commit("Alice", {"gone.py": None})
    h = mine_repo(repo.path)
    assert h.head_files == ["a.py"]
    assert all("gone.py" not in c.files for c in h.commits)


def test_recreated_path_is_a_new_file(repo):
    repo.commit("Alice", {"x.py": body(0)})
    repo.commit("Alice", {"x.py": None})
    repo.commit("Bob", {"x.py": "print('new file')\n"})
    h = mine_repo(repo.path)
    credited = [c for c in h.commits if c.files]
    assert len(credited) == 1 and h.developers[credited[0].dev_id].names == ["Bob"]


def test_bots_are_not_credited(repo):
    repo.commit("Alice", {"a.py": body(0)})
    repo.commit("dependabot[bot]", {"a.py": body(1)}, email="49699333+dependabot[bot]@users.noreply.github.com")
    h = mine_repo(repo.path)
    assert [d.names for d in h.developers.values()] == [["Alice"]]


def test_bulk_commits_are_skipped_but_renames_still_tracked(repo):
    repo.commit("Alice", {f"m{i}.py": body(i) for i in range(4)})
    # A "reformat" commit touching 5 files (> limit of 4), including a rename.
    repo.commit("Carol", {**{f"m{i}.py": body(i) + "\n" for i in range(1, 4)}, "extra.py": body(9)},
                rename=("m0.py", "core.py"))
    h = mine_repo(repo.path, bulk_limit=4)
    assert [d.names for d in h.developers.values()] == [["Alice"]]
    assert "core.py" in h.commits[0].added


def test_non_source_files_are_dropped():
    assert is_source("src/app.py")
    assert not is_source("package-lock.json")
    assert not is_source("vendor/lib.py")
    assert not is_source("static/app.min.js")
    assert not is_source("README.md")


def test_identity_merge():
    ids = {
        ("Jane Doe", "jane@work.com"),
        ("jane  doe", "jane@home.com"),
        ("JD", "12345+janedoe@users.noreply.github.com"),
        ("Jane D", "janedoe@gmail.com"),
        ("Bob", "info@bob.com"),
        ("Carol", "info@carol.com"),  # same local-part, different person
    }
    canon = merge_identities(ids)
    assert canon[("Jane Doe", "jane@work.com")] == canon[("jane  doe", "jane@home.com")]
    assert canon[("JD", "12345+janedoe@users.noreply.github.com")] == canon[("Jane D", "janedoe@gmail.com")]
    assert canon[("Bob", "info@bob.com")] != canon[("Carol", "info@carol.com")]


def test_same_person_two_emails_is_one_developer(repo):
    repo.commit("Alice Smith", {"a.py": body(0)}, email="alice@work.com")
    repo.commit("Alice Smith", {"a.py": body(1)}, email="alice@home.com")
    h = mine_repo(repo.path)
    assert len(h.developers) == 1
    assert h.developers["d1"].emails == ["alice@home.com", "alice@work.com"]
