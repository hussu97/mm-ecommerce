from scripts.foodics.extract_inventory_snapshot import _ingredient_lines, sanitize
from scripts.foodics.stage_inventory_snapshot import _canonical_unit, _yield


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


def test_detail_recipe_lines_preserve_owner_and_foodics_pivot_values():
    assert _ingredient_lines(
        owner_id="brownie-id",
        owner_field="inventory_item_id",
        detail={
            "ingredients": [
                {
                    "id": "butter-id",
                    "pivot": {"quantity": 14.0625, "yield_percentage": 100},
                }
            ]
        },
    ) == [
        {
            "id": "brownie-id:butter-id",
            "inventory_item_id": "brownie-id",
            "ingredient_id": "butter-id",
            "quantity": 14.0625,
            "yield_percentage": 100,
            "inactive_in_order_types": None,
            "source_pivot": {"quantity": 14.0625, "yield_percentage": 100},
        }
    ]


def test_staging_normalizes_foodics_units_and_percentage_yields():
    assert _canonical_unit("Gram") == "g"
    assert _canonical_unit("Nos") == "unit"
    assert _canonical_unit("Piece") == "unit"
    assert str(_yield(100)) == "1"
    assert str(_yield("75")) == "0.75"
