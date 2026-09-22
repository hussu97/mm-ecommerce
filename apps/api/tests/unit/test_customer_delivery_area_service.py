from app.services import customer_delivery_area_service as service


def test_geometry_contains_polygon_and_excludes_a_hole():
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [[55.0, 25.0], [55.2, 25.0], [55.2, 25.2], [55.0, 25.2], [55.0, 25.0]],
            [
                [55.08, 25.08],
                [55.12, 25.08],
                [55.12, 25.12],
                [55.08, 25.12],
                [55.08, 25.08],
            ],
        ],
    }

    assert service._geometry_contains(geometry, 55.04, 25.04)
    assert not service._geometry_contains(geometry, 55.1, 25.1)
    assert not service._geometry_contains(geometry, 55.3, 25.3)


def test_geometry_contains_multipolygon():
    geometry = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[55.0, 25.0], [55.1, 25.0], [55.1, 25.1], [55.0, 25.1], [55.0, 25.0]]],
            [[[55.2, 25.2], [55.3, 25.2], [55.3, 25.3], [55.2, 25.3], [55.2, 25.2]]],
        ],
    }

    assert service._geometry_contains(geometry, 55.25, 25.25)
