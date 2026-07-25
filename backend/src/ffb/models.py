from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ffb.db import Base


class User(Base):
    __tablename__ = "users"

    # Keyed on the Sleeper user id: usernames are mutable, ids are not.
    sleeper_user_id: Mapped[str] = mapped_column(String, primary_key=True)
    sleeper_username: Mapped[str] = mapped_column(String, index=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    avatar: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


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
