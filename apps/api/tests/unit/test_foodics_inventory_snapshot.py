from scripts.foodics.extract_inventory_snapshot import sanitize


def test_snapshot_sanitizer_removes_nested_credentials_only():
    payload = {
        "id": "item-1",
        "token": "do-not-write",
        "nested": [{"name": "Flour", "authorization": "secret"}],
    }

    assert sanitize(payload) == {
        "id": "item-1",
        "nested": [{"name": "Flour"}],
    }
