"""v7 DJ automix: per-track analysis detail and reordering a list of tracks for smooth flow.

The compact analysis (beat grid, key, mix points) is in every Track as `mix`; clients plan transitions from that.
These endpoints add the energy curve (for UI) and the ordering used by "Automix" on radio, mixes and playlists.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from nasmusic.app import ai_adapter
from nasmusic.app.context import AppContext, ctx, db, device
from nasmusic.auth import Device
from nasmusic.core import ai_config
from nasmusic.library import queries

router = APIRouter(dependencies=[Depends(device)])


class OrderBody(BaseModel):
    trackIds: list[str] = Field(max_length=1000)
    keepFirst: bool = True
    window: int = Field(default=6, ge=1, le=50)
    arc: bool = True


@router.post("/automix/order")
def automix_order(body: OrderBody, me: Device = Depends(device), conn=Depends(db), c: AppContext = Depends(ctx)):
    ids = list(body.trackIds)
    if len(ids) <= 2:
        return {"trackIds": ids, "transitions": []}
    try:
        ordered, transitions = ai_adapter.automix_order(conn, ai_config(c.cfg), ids, window=body.window,
                                                        keep_first=body.keepFirst, arc=body.arc)
    except NotImplementedError as e:
        raise HTTPException(503, "automix isn't available yet") from e
    return {"trackIds": ordered, "transitions": transitions}


@router.get("/tracks/{track_id}/mix")
def track_mix(track_id: str, me: Device = Depends(device), conn=Depends(db), c: AppContext = Depends(ctx)):
    if queries.track(conn, me.user_id, track_id) is None:
        raise HTTPException(404, "no such track")
    d = ai_adapter.track_mix_detail(conn, ai_config(c.cfg), track_id)
    if d is None:
        raise HTTPException(404, "not analysed for automix yet")
    return d


def order_for_queue(conn, cfg, ids: list[str], keep_first: bool) -> list[str]:
    """Radio helper: reorder for flow without an energy arc (radio is endless). Never fails the caller."""
    try:
        ordered, _ = ai_adapter.automix_order(conn, ai_config(cfg), ids, window=8, keep_first=keep_first, arc=False)
        return ordered if sorted(ordered) == sorted(ids) else ids
    except Exception:  # noqa: BLE001 - ordering is a nicety
        return ids



