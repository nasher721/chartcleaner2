from chartcleaner.editor_sync import EditorRevisions


def test_editor_revisions_accept_only_newer_snapshots():
    revisions = EditorRevisions()
    assert revisions.accept(1)
    assert revisions.accept(2)
    assert not revisions.accept(2)
    assert not revisions.accept(1)
    assert revisions.accept(3)


def test_editor_revisions_reject_non_integers_and_bool():
    revisions = EditorRevisions()
    for value in (True, 1.0, "1", None):
        assert not revisions.accept(value)
    assert revisions.accept(1)
