"""
Admin control of the delivery map: what each zone costs and who carries it.

Maps are versioned and a published one is read-only. Changing a fee means
cloning the live map into a draft, editing the draft, and publishing it —
which makes rolling back a single click on yesterday's version rather than an
attempt to remember what the numbers used to be.

Geometry is deliberately absent from the list responses. The Abu Dhabi outline
alone is four and a half thousand points, and an admin comparing fees does not
need to download the coastline to do it.

This was one 1,581-line module serving several resource families behind one
prefix — versions and their zones, couriers, and the shop-wide settings row.
Each has its own `APIRouter` here, so the routes and their prefix are unchanged.
"""

from fastapi import APIRouter

from . import couriers, settings, versions

router = APIRouter()
# Include order does not affect matching here: every route in every group
# begins with a distinct literal segment (`/versions`, `/couriers`, `/settings`,
# `/map`, `/polygons`, `/summary`) and none of the groups declares a bare
# `/{param}`. Adding one would change that, so add it to `versions` and keep it
# last.
router.include_router(versions.router)
router.include_router(couriers.router)
router.include_router(settings.router)

__all__ = ["router"]
