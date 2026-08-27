"""evaluate_in_page falls back when the driver has no isolated_context kwarg."""

from aggregator_bootstrap.engine import evaluate_in_page


class _PlaywrightLike:
    async def evaluate(self, script: str, arg: object = None):
        return ("plain", script[:12], arg)


class _PatchrightLike:
    async def evaluate(self, script: str, arg: object = None, *, isolated_context: bool = True):
        return ("isolated", isolated_context, arg)


async def test_evaluate_uses_main_world_when_supported():
    assert await evaluate_in_page(_PatchrightLike(), "() => 1") == ("isolated", False, None)
    assert await evaluate_in_page(_PatchrightLike(), "() => 1", {"x": 1}) == (
        "isolated",
        False,
        {"x": 1},
    )


async def test_evaluate_falls_back_without_the_kwarg():
    assert await evaluate_in_page(_PlaywrightLike(), "() => 1") == ("plain", "() => 1", None)
    assert await evaluate_in_page(_PlaywrightLike(), "() => 1", 7) == ("plain", "() => 1", 7)
