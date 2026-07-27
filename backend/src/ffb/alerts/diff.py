"""Turn injury reports into the short list of things worth saying.

Kept free of nflverse, Sleeper and the database so the interesting part - what
counts as a change - can be tested directly.
"""

from dataclasses import dataclass, field

# Ordered worst to best. Used to decide whether a change is a downgrade.
SEVERITY = ["Out", "Doubtful", "Questionable"]


@dataclass(frozen=True)
class RosteredPlayer:
    sleeper_player_id: str
    name: str
    position: str
    nfl_team: str
    # A player can sit on rosters in more than one league.
    leagues: tuple[str, ...] = ()


@dataclass(frozen=True)
class Alert:
    player: RosteredPlayer
    previous: str | None
    current: str | None
    week: int

    @property
    def cleared(self) -> bool:
        return self.current is None

    @property
    def worsened(self) -> bool:
        """True when the new status is more serious than the old one."""
        if self.current is None:
            return False
        if self.previous is None:
            return True
        return _rank(self.current) < _rank(self.previous)

    def line(self) -> str:
        where = f" ({', '.join(self.player.leagues)})" if self.player.leagues else ""
        who = f"{self.player.name} {self.player.position} {self.player.nfl_team}".strip()
        if self.cleared:
            return f"✅ {who}{where} - off the report (was {self.previous})"
        arrow = "🔻" if self.worsened else "🔁"
        if self.previous is None:
            return f"{arrow} {who}{where} - {self.current}"
        return f"{arrow} {who}{where} - {self.previous} -> {self.current}"


def _rank(status: str) -> int:
    """Lower is worse. Unknown statuses sort just better than Questionable."""
    for i, known in enumerate(SEVERITY):
        if status.lower().startswith(known.lower()):
            return i
    return len(SEVERITY)


@dataclass
class DiffResult:
    alerts: list[Alert] = field(default_factory=list)
    # sleeper_player_id -> status to persist. None means delete the row.
    next_state: dict[str, str | None] = field(default_factory=dict)


def diff_statuses(
    rostered: dict[str, RosteredPlayer],
    current: dict[str, str],
    previous: dict[str, str],
    week: int,
) -> DiffResult:
    """Compare this week's report against what we last announced.

    `current` holds only players who appear on the injury report with a status
    worth mentioning; anyone rostered and absent from it is healthy. `previous`
    is what we said last time. Only differences come back as alerts, so running
    this repeatedly on an unchanged report stays silent.
    """
    result = DiffResult()

    for player_id, player in rostered.items():
        now = current.get(player_id)
        before = previous.get(player_id)

        if now == before:
            if now is not None:
                result.next_state[player_id] = now
            continue

        if now is None:
            # Was flagged, no longer on the report.
            result.alerts.append(Alert(player, before, None, week))
            result.next_state[player_id] = None
        else:
            result.alerts.append(Alert(player, before, now, week))
            result.next_state[player_id] = now

    # Worst news first, then alphabetically so the message is stable.
    result.alerts.sort(key=lambda a: (a.cleared, _rank(a.current or ""), a.player.name))
    return result


def format_message(alerts: list[Alert], week: int) -> str:
    if not alerts:
        return ""
    changed = [a for a in alerts if not a.cleared]
    cleared = [a for a in alerts if a.cleared]
    head = f"**Injury report - week {week}**"
    parts = [head]
    parts.extend(a.line() for a in changed)
    parts.extend(a.line() for a in cleared)
    return "\n".join(parts)
