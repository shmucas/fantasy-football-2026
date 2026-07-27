from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ffb.db import Base


class League(Base):
    __tablename__ = "leagues"

    key: Mapped[str] = mapped_column(String, primary_key=True)  # "miller_league_hs" | "maxxing_college"
    name: Mapped[str] = mapped_column(String)
    sleeper_league_id: Mapped[str] = mapped_column(String)
    season: Mapped[str] = mapped_column(String)
    num_teams: Mapped[int] = mapped_column(Integer)
    friend_group: Mapped[str] = mapped_column(String)


class Player(Base):
    __tablename__ = "players"

    player_id: Mapped[str] = mapped_column(String, primary_key=True)  # Sleeper player_id
    name: Mapped[str] = mapped_column(String)
    position: Mapped[str] = mapped_column(String)
    nfl_team: Mapped[str | None] = mapped_column(String, nullable=True)
    bye_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proj_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    proj_stdev: Mapped[float | None] = mapped_column(Float, nullable=True)
    adp: Mapped[float | None] = mapped_column(Float, nullable=True)


class Roster(Base):
    __tablename__ = "rosters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_key: Mapped[str] = mapped_column(ForeignKey("leagues.key"))
    sleeper_roster_id: Mapped[int] = mapped_column(Integer)
    owner_display_name: Mapped[str] = mapped_column(String)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.player_id"))


class InjuryState(Base):
    """Last injury status we announced for a rostered player.

    The alerter posts changes, not standing state, so it needs to remember what
    it said last time. One row per player per season; absence of a row means we
    have never flagged that player.
    """

    __tablename__ = "injury_state"

    season: Mapped[str] = mapped_column(String, primary_key=True)
    sleeper_player_id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String)
    week: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    # Kept so a player who drops off the report can still be named, without
    # refetching Sleeper's 5MB player dictionary just to resolve one id.
    name: Mapped[str] = mapped_column(String, default="")
    position: Mapped[str] = mapped_column(String, default="")
    nfl_team: Mapped[str] = mapped_column(String, default="")


class PendingAction(Base):
    """Something the bot wants to do to a Sleeper roster, awaiting a human yes.

    The row is the record of intent *and* the permission slip. `payload` holds
    the exact GraphQL body that will be sent, decided at proposal time, so what
    gets approved is what gets executed - the plan cannot be rewritten between
    the two. `approval_token` is the unguessable single-use secret that appears
    in the Discord link; possession of it is what authorises the action, since
    the web app has no login.
    """

    __tablename__ = "pending_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String)  # set_starters | waiver_claim | add_drop | trade
    league_id: Mapped[str] = mapped_column(String)
    league_name: Mapped[str] = mapped_column(String, default="")
    roster_id: Mapped[int] = mapped_column(Integer)
    # Human-readable one-liner: what a person is actually agreeing to.
    summary: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(String, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)

    status: Mapped[str] = mapped_column(String, default="proposed")
    approval_token: Mapped[str] = mapped_column(String, unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Sleeper's transaction_id on success, or the error text on failure.
    result: Mapped[str] = mapped_column(String, default="")
