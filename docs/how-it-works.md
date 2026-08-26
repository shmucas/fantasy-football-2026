# How the math works

The reasoning behind the numbers the app shows.
See the [README](../README.md) for how to run any of it.

### Player value: VORP

A player's raw projection doesn't say much on its own - what matters is how
much better they are than the player you'd get for free at the same
position. We compute **replacement level** per position: the projection of
the last starter-worthy player at that position, league-wide, given the
roster rules and number of teams (flex slots are allocated to whichever
position has the best marginal player at that depth). Then:

```
VORP(player) = player.proj_points - replacement_level[player.position]
```

This is what ranks the "Suggested next pick" list and picks the sample
roster's picks in the UI.

### Opponent draft model

Each simulated opponent doesn't just take the top of the ADP board - real
drafts have noise (reaches, sleepers) and are shaped by roster need. For each
available player, we compute a noisy effective draft slot:

```
effective_slot = ADP + Normal(0, sigma) + need_penalty
```

The opponent takes the player with the lowest effective slot.

- `sigma = max(round_sigma(round), player.adp_stdev)` - noise grows by round
  (`SIGMA_BASE + SIGMA_PER_ROUND * (round - 1)`), so round 1 is close to
  chalk and late rounds are noisy reaches. A player's own ADP volatility (from
  FFC) sets a floor, so genuinely unpredictable players stay unpredictable.
- `need_penalty` adds `NEED_PENALTY_SLOTS` (25 spots) once a position's
  starting requirement (including flex capacity) is already filled on that
  team's roster, pushing the opponent off positions they don't need.

### Draft simulation

One simulated draft plays the snake order pick by pick. Any pick already
logged on the Live Draft Board is replayed exactly as it happened (not
simulated). Your own forced picks are reserved ahead of time so they can't be
sniped by an opponent. Everything else runs through the opponent model above.
Running `--n-sims` (or the UI's simulation count) of these and looking at the
distribution of your team's projected points (mean, stdev, P10/P50/P90) is
how "Scenario Results" gets built.

### Season simulation: points to wins

Total projected points is a rough score - what you actually want is win
probability. The season simulator sim samples a full season per draft outcome:

1. **Season factor**, once per player: `season_level = ppg * (1 + z_season * season_cv)`,
   where `ppg = proj_points / 17`.
2. **Weekly score**, per player per week, conditioned on that season level:
   `week_score = season_level * (1 + z_game * game_cv)` (clamped at 0).
3. Each team starts its best lineup **by projection** (not by the score
   about to be rolled - you don't get to see the future when setting a
   lineup), and plays a fixed round-robin schedule against the other slots.
4. Wins are counted from head-to-head weekly totals.

Two variance-reduction tricks make this cheap and fair to compare scenarios
with:

- **Common random numbers**: every player's random draws are keyed by
  `(seed, player index)` only - identical across different forced-pick
  scenarios. So when you compare "what if I take Chase in round 1" vs "what
  if I take CMC," every other team and every weekly roll is held fixed, and
  only your pick differs. Real (small) differences don't get drowned out by
  independent noise.
- **Latin hypercube sampling** on the season factor: instead of drawing
  `n_samples` season z-values independently at random, we take evenly spaced
  quantiles and randomly permute them per player. This spreads samples evenly
  across the distribution, so you need fewer samples for a stable estimate.
  Weekly (game-level) draws are left as plain i.i.d. draws since they average
  out fast across 14 weeks anyway.
