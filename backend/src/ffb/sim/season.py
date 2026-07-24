"""Nested season simulation -> expected regular-season wins for our team.

Each sample is one full hierarchical season (flat O(N) sampling, not D*n*m
nested loops - expected wins is a single expectation over the joint noise):

  1. season factor drawn ONCE per player: season_level = ppg * (1 + z_s * season_cv)
  2. weekly score drawn per player per week, conditioned on the season level:
     week_score = season_level * (1 + z_g * game_cv)
  3. each team starts its best lineup BY PROJECTION (not realized scores), and
     head-to-head matchups are played over a fixed round-robin schedule.

Two variance levers:
  - Common Random Numbers: a player's draws are keyed by their stable index and
    the sample seed only, so they are identical across forced-pick scenarios -
    the shared noise cancels when comparing picks.
  - Latin hypercube on the season dimension: per player, season z-values across
    samples are a stratified permutation (1-D LHS), where variance reduction
    helps most. Game draws are iid (they average out).
"""

import numpy as np

from ffb.draft.sim import select_starters
from ffb.draft.strategy import Player

REG_SEASON_WEEKS = 14
POINTS_FLOOR = 0.0  # weekly scores clamp at zero


def round_robin(num_teams: int, weeks: int) -> list[list[tuple[int, int]]]:
    """Balanced schedule of (slot_a, slot_b) matchups per week (circle method)."""
    slots: list[int | None] = list(range(1, num_teams + 1))
    if num_teams % 2:
        slots.append(None)  # odd -> one bye per week
    m = len(slots)
    arr = slots[:]
    schedule = []
    for _ in range(weeks):
        pairs = [
            (arr[i], arr[m - 1 - i])
            for i in range(m // 2)
            if arr[i] is not None and arr[m - 1 - i] is not None
        ]
        schedule.append(pairs)
        arr = [arr[0], arr[-1], *arr[1:-1]]  # rotate, first fixed
    return schedule


def season_z_matrix(n_samples: int, n_players: int, seed: int = 0) -> np.ndarray:
    """(n_samples x n_players) standard-normal season factors, stratified per
    player (1-D Latin hypercube) so each player's draws span the distribution
    evenly across samples. Deterministic in (seed, player index) -> gives CRN."""
    from scipy.stats import norm

    quantiles = norm.ppf((np.arange(n_samples) + 0.5) / n_samples)
    out = np.empty((n_samples, n_players))
    for p in range(n_players):
        rng = np.random.default_rng((seed, p))
        out[:, p] = rng.permutation(quantiles)
    return out


class SeasonModel:
    """Holds the stable player universe and precomputed season draws, shared
    across all scenarios so comparisons use common random numbers."""

    def __init__(self, all_players: list[Player], n_samples: int, seed: int = 0):
        self.n_samples = n_samples
        self.index = {p.player_id: i for i, p in enumerate(all_players)}
        n = len(all_players)
        self.ppg = np.array([p.proj_points / 17.0 for p in all_players])
        self.season_cv = np.array([p.season_cv for p in all_players])
        self.game_cv = np.array([p.game_cv for p in all_players])
        self.season_z = season_z_matrix(n_samples, n, seed)

    def _team_starter_idx(self, roster, roster_positions) -> list[int]:
        return [self.index[p.player_id] for p in select_starters(roster, roster_positions)]

    def evaluate(
        self,
        rosters: dict[int, list[Player]],
        roster_positions: list[str],
        my_slot: int,
        schedule: list[list[tuple[int, int]]],
        sample_i: int,
    ) -> tuple[int, float]:
        """Play one sampled season; return (my_wins, my_points_for)."""
        starters = {slot: self._team_starter_idx(r, roster_positions) for slot, r in rosters.items()}
        weeks = len(schedule)

        game_rng = np.random.default_rng((10_000_000, sample_i))  # CRN: keyed by sample only
        sz = self.season_z[sample_i]  # (n_players,)
        season_level = np.maximum(self.ppg * (1.0 + sz * self.season_cv), POINTS_FLOOR)

        # Weekly realized score per player: (n_players x weeks)
        z_game = game_rng.standard_normal((len(self.ppg), weeks))
        weekly = np.maximum(
            season_level[:, None] * (1.0 + z_game * self.game_cv[:, None]), POINTS_FLOOR
        )

        team_week_totals = {
            slot: weekly[idx, :].sum(axis=0) if idx else np.zeros(weeks)
            for slot, idx in starters.items()
        }

        my_wins = 0
        my_pf = 0.0
        for w, pairs in enumerate(schedule):
            for a, b in pairs:
                if my_slot not in (a, b):
                    continue
                opp = b if a == my_slot else a
                mine, theirs = team_week_totals[my_slot][w], team_week_totals[opp][w]
                my_pf += mine
                if mine > theirs:
                    my_wins += 1
        return my_wins, float(my_pf)
