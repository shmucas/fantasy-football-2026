import { useEffect, useState } from "react";
import "./App.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8010/api";

// Team codes disagree across our data sources (nflverse ids, nflverse schedules,
// Sleeper's own CDN) - normalize everything to the codes Sleeper's logo CDN expects.
const TEAM_ALIASES: Record<string, string> = {
  GBP: "GB",
  JAC: "JAX",
  KCC: "KC",
  LVR: "LV",
  NEP: "NE",
  NOS: "NO",
  SFO: "SF",
  TBB: "TB",
  LA: "LAR",
  OAK: "LV",
  SDC: "LAC",
  STL: "LAR",
  RAM: "LAR",
  FA: "",
  "FA*": "",
};

function normalizeTeam(code: string): string {
  return TEAM_ALIASES[code] ?? code;
}

function teamLogoUrl(code: string): string {
  const team = normalizeTeam(code);
  return team ? `https://sleepercdn.com/images/team_logos/nfl/${team.toLowerCase()}.png` : "";
}

// FFC's ADP source has no stable id or team code for defenses - the only
// reliable signal is the "<City> Defense" name, so map that to a team code.
const DEF_CITY_TO_TEAM: Record<string, string> = {
  Arizona: "ARI", Atlanta: "ATL", Baltimore: "BAL", Buffalo: "BUF",
  Carolina: "CAR", Chicago: "CHI", Cincinnati: "CIN", Cleveland: "CLE",
  Dallas: "DAL", Denver: "DEN", Detroit: "DET", "Green Bay": "GB",
  Houston: "HOU", Indianapolis: "IND", Jacksonville: "JAX",
  "Kansas City": "KC", "LA Chargers": "LAC", "LA Rams": "LAR", "Las Vegas": "LV",
  Miami: "MIA", Minnesota: "MIN", "New England": "NE", "New Orleans": "NO",
  "NY Giants": "NYG", "NY Jets": "NYJ", Philadelphia: "PHI", Pittsburgh: "PIT",
  Seattle: "SEA", "San Francisco": "SF", "Tampa Bay": "TB", Tennessee: "TEN",
  Washington: "WAS",
};

function avatarUrl(playerId: string, position?: string, name?: string): string {
  if (position === "DEF") {
    const city = name?.replace(/ Defense$/, "") ?? "";
    return teamLogoUrl(DEF_CITY_TO_TEAM[city] ?? playerId);
  }
  return `https://sleepercdn.com/content/nfl/players/${playerId}.jpg`;
}

type LeagueConfig = {
  key: string;
  name: string;
  season: string;
  num_teams: number;
  friend_group: string;
  roster_positions: string[];
  faab: boolean;
};

type Roster = {
  status: string;
  display_name: string;
  roster_id: number;
  player_ids: string[];
};

type ScenarioStats = {
  scenario: string;
  mean: number;
  stdev: number;
  p10: number;
  p50: number;
  p90: number;
};

type Player = {
  player_id: string;
  name: string;
  position: string;
  proj_points: string;
  proj_stdev: string;
  adp: string;
  adp_stdev: string;
  nfl_team: string;
};

type Recommendation = {
  player_id: string;
  name: string;
  position: string;
  proj_points: number;
  vorp: number;
  reason: string;
};

type WaiverRecommendation = Recommendation & {
  nfl_team: string;
  fills_need: boolean;
};

type SamplePick = {
  round: number;
  player_id: string;
  name: string;
  position: string;
  reason: string;
};

type Game = {
  game_id: string;
  game_type: string;
  week: number;
  gameday: string;
  weekday: string;
  gametime: string | null;
  away_team: string;
  away_score: number | null;
  home_team: string;
  home_score: number | null;
};

type SessionUser = {
  sleeper_user_id: string;
  sleeper_username: string;
  display_name: string | null;
  avatar: string | null;
};

function Login({ onSignedIn }: { onSignedIn: (user: SessionUser) => void }) {
  const [username, setUsername] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ username: username.trim() }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail ?? "Couldn't sign you in");
      }
      onSignedIn(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't sign you in");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="header">
        <div>
          <h1>FFB Draft Simulator</h1>
          <p>Sign in with your Sleeper username to get started</p>
        </div>
      </div>

      <div className="card login-card">
        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor="sleeper-username">Sleeper username</label>
            <input
              id="sleeper-username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="e.g. lucaspedroferreira"
              autoFocus
            />
          </div>
          <button className="run-btn" type="submit" disabled={busy || !username.trim()}>
            {busy ? "Checking Sleeper..." : "Sign in"}
          </button>
          {error && <p className="state-msg error">{error}</p>}
        </form>
      </div>
    </>
  );
}

type WinBucket = {
  wins: number;
  pct: number;
};

type SeasonScenario = {
  scenario: string;
  forced_picks: string[];
  exp_wins: number;
  win_stdev: number;
  win_distribution: WinBucket[];
  threshold_wins: number;
  threshold_pct: number;
  avg_points: number;
  points_p10: number;
  points_p50: number;
  points_p90: number;
};

type SeasonSim = {
  league_key: string;
  my_slot: number;
  n_samples: number;
  rounds: number;
  reg_season_weeks: number;
  scenarios: SeasonScenario[];
};

const SERIES = ["#2a78d6", "#eb6834"];

function scenarioLabel(s: SeasonScenario): string {
  return s.scenario === "baseline"
    ? "Baseline draft"
    : `With my forced picks (${s.forced_picks.join(", ")})`;
}

function WinDistributionChart({ scenarios }: { scenarios: SeasonScenario[] }) {
  const width = 720;
  const height = 260;
  const pad = { top: 16, right: 12, bottom: 34, left: 40 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const buckets = scenarios[0].win_distribution.length;
  const maxPct = Math.max(...scenarios.flatMap((s) => s.win_distribution.map((b) => b.pct)), 5);
  const yMax = Math.ceil(maxPct / 5) * 5;
  const groupW = plotW / buckets;
  const barW = Math.max(3, (groupW - 6) / scenarios.length - 2);
  const ticks = [0, yMax / 2, yMax];

  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img">
      {ticks.map((t) => {
        const y = pad.top + plotH - (t / yMax) * plotH;
        return (
          <g key={t}>
            <line className="chart-grid" x1={pad.left} x2={width - pad.right} y1={y} y2={y} />
            <text className="chart-axis-label" x={pad.left - 8} y={y + 4} textAnchor="end">
              {t.toFixed(0)}%
            </text>
          </g>
        );
      })}
      {scenarios[0].win_distribution.map((b, i) => (
        <text
          key={b.wins}
          className="chart-axis-label"
          x={pad.left + groupW * i + groupW / 2}
          y={height - 12}
          textAnchor="middle"
        >
          {b.wins}
        </text>
      ))}
      {scenarios.map((s, si) =>
        s.win_distribution.map((b, i) => {
          const h = (b.pct / yMax) * plotH;
          const x = pad.left + groupW * i + 3 + si * (barW + 2);
          return (
            <rect
              key={`${s.scenario}-${b.wins}`}
              x={x}
              y={pad.top + plotH - h}
              width={barW}
              height={Math.max(h, 0)}
              rx={2}
              fill={SERIES[si % SERIES.length]}
            >
              <title>{`${scenarioLabel(s)}: ${b.wins} wins in ${b.pct.toFixed(1)}% of seasons`}</title>
            </rect>
          );
        })
      )}
      <line
        className="chart-axis"
        x1={pad.left}
        x2={width - pad.right}
        y1={pad.top + plotH}
        y2={pad.top + plotH}
      />
    </svg>
  );
}

function App() {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    fetch(`${API}/auth/me`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setChecking(false));
  }, []);

  async function signOut() {
    await fetch(`${API}/auth/logout`, { method: "POST", credentials: "include" }).catch(
      () => undefined,
    );
    setUser(null);
  }

  if (checking) return <p className="state-msg">Loading...</p>;
  if (!user) return <Login onSignedIn={setUser} />;
  return <DraftApp user={user} onSignOut={signOut} />;
}

function DraftApp({ user, onSignOut }: { user: SessionUser; onSignOut: () => void }) {
  const [leagues, setLeagues] = useState<LeagueConfig[]>([]);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [roster, setRoster] = useState<Roster | null>(null);
  const [rosterError, setRosterError] = useState<string | null>(null);
  const [results, setResults] = useState<ScenarioStats[]>([]);
  const [mySlot, setMySlot] = useState(4);
  const [nSims, setNSims] = useState(500);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [players, setPlayers] = useState<Player[]>([]);
  const [posFilter, setPosFilter] = useState<string>("ALL");
  const [forcedPicks, setForcedPicks] = useState<{ round: number; playerId: string }[]>([]);
  const [draftLog, setDraftLog] = useState<string[]>([]);
  const [nextPickPlayer, setNextPickPlayer] = useState("");
  const [teamNames, setTeamNames] = useState<Record<number, string>>({});
  const [lastSample, setLastSample] = useState<SamplePick[]>([]);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [view, setView] = useState<"draft" | "waivers" | "schedule" | "sims">("draft");
  const [waivers, setWaivers] = useState<Player[]>([]);
  const [waiversLoading, setWaiversLoading] = useState(false);
  const [waiverRecs, setWaiverRecs] = useState<WaiverRecommendation[]>([]);
  const [waiverRecsLoading, setWaiverRecsLoading] = useState(false);
  const [weeks, setWeeks] = useState<number[]>([]);
  const [scheduleWeek, setScheduleWeek] = useState<number>(1);
  const [games, setGames] = useState<Game[]>([]);
  const [scheduleLoading, setScheduleLoading] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [seasonSim, setSeasonSim] = useState<SeasonSim | null>(null);
  const [seasonRunning, setSeasonRunning] = useState(false);
  const [seasonError, setSeasonError] = useState<string | null>(null);
  const [nSamples, setNSamples] = useState(300);

  const active = leagues.find((l) => l.key === activeKey) ?? null;

  useEffect(() => {
    fetch(`${API}/leagues`, { credentials: "include" })
      .then((r) => r.json())
      .then((data: LeagueConfig[]) => {
        setLeagues(data);
        if (data.length) setActiveKey(data[0].key);
      })
      .catch(() => setLeagues([]));
  }, []);

  useEffect(() => {
    if (!activeKey) return;
    setRoster(null);
    setRosterError(null);
    fetch(`${API}/leagues/${activeKey}/roster`, { credentials: "include" })
      .then((r) => {
        if (!r.ok) throw new Error("Couldn't reach Sleeper for this league");
        return r.json();
      })
      .then(setRoster)
      .catch((e) => setRosterError(e.message));

    setPosFilter("ALL");
    fetch(`${API}/leagues/${activeKey}/players`, { credentials: "include" })
      .then((r) => r.json())
      .then(setPlayers)
      .catch(() => setPlayers([]));

    setForcedPicks([]);
    setDraftLog([]);
    setWaivers([]);
    setLastSample([]);
    setSeasonSim(null);
    setSeasonError(null);
    fetch(`${API}/leagues/${activeKey}/draft-order`, { credentials: "include" })
      .then((r) => r.json())
      .then(setTeamNames)
      .catch(() => setTeamNames({}));
    loadResults(activeKey);
  }, [activeKey]);

  useEffect(() => {
    if (!activeKey) return;
    const excluded = [
      ...draftLog,
      ...forcedPicks.filter((f) => f.playerId).map((f) => f.playerId),
    ];
    fetch(`${API}/leagues/${activeKey}/recommend?exclude=${excluded.join(",")}`, { credentials: "include" })
      .then((r) => r.json())
      .then(setRecommendations)
      .catch(() => setRecommendations([]));
  }, [activeKey, forcedPicks, draftLog]);

  useEffect(() => {
    if (!activeKey || view !== "waivers") return;
    setWaiversLoading(true);
    fetch(`${API}/leagues/${activeKey}/waivers`, { credentials: "include" })
      .then((r) => r.json())
      .then(setWaivers)
      .catch(() => setWaivers([]))
      .finally(() => setWaiversLoading(false));

    setWaiverRecsLoading(true);
    fetch(`${API}/leagues/${activeKey}/waivers/recommend`, { credentials: "include" })
      .then((r) => r.json())
      .then(setWaiverRecs)
      .catch(() => setWaiverRecs([]))
      .finally(() => setWaiverRecsLoading(false));
  }, [activeKey, view]);

  useEffect(() => {
    if (!activeKey || view !== "schedule") return;
    fetch(`${API}/leagues/${activeKey}/schedule/weeks`, { credentials: "include" })
      .then((r) => r.json())
      .then((data: number[]) => {
        setWeeks(data);
        if (data.length && !data.includes(scheduleWeek)) setScheduleWeek(data[0]);
      })
      .catch(() => setWeeks([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey, view]);

  useEffect(() => {
    if (!activeKey || view !== "schedule") return;
    setScheduleLoading(true);
    fetch(`${API}/leagues/${activeKey}/schedule/${scheduleWeek}`, { credentials: "include" })
      .then((r) => r.json())
      .then(setGames)
      .catch(() => setGames([]))
      .finally(() => setScheduleLoading(false));
  }, [activeKey, view, scheduleWeek]);

  function loadResults(key: string) {
    fetch(`${API}/leagues/${key}/results`, { credentials: "include" })
      .then((r) => r.json())
      .then(setResults)
      .catch(() => setResults([]));
  }

  async function runSim() {
    if (!activeKey) return;
    setRunning(true);
    setRunError(null);
    try {
      const res = await fetch(`${API}/sims/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          league_key: activeKey,
          my_slot: mySlot,
          n_sims: nSims,
          forced_picks: Object.fromEntries(
            forcedPicks.filter((f) => f.playerId).map((f) => [f.round, f.playerId])
          ),
          already_picked: draftLog,
        }),
      });
      if (!res.ok) throw new Error("Simulation failed to run");
      const data = await res.json();
      setLastSample(data.sample_roster ?? []);
      loadResults(activeKey);
    } catch (e) {
      setRunError(e instanceof Error ? e.message : "Simulation failed to run");
    } finally {
      setRunning(false);
    }
  }

  async function runSeasonSim() {
    if (!activeKey) return;
    setSeasonRunning(true);
    setSeasonError(null);
    try {
      const res = await fetch(`${API}/leagues/${activeKey}/sims/season`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          my_slot: mySlot,
          n_samples: nSamples,
          forced_picks: Object.fromEntries(
            forcedPicks.filter((f) => f.playerId).map((f) => [f.round, f.playerId])
          ),
          already_picked: draftLog,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Season simulation failed");
      setSeasonSim(await res.json());
    } catch (e) {
      setSeasonError(e instanceof Error ? e.message : "Season simulation failed");
    } finally {
      setSeasonRunning(false);
    }
  }

  const playerById = new Map(players.map((p) => [p.player_id, p]));
  const positions = ["ALL", ...Array.from(new Set(players.map((p) => p.position))).sort()];
  const shownPlayers = (posFilter === "ALL" ? players : players.filter((p) => p.position === posFilter))
    .slice()
    .sort((a, b) => Number(a.adp) - Number(b.adp))
    .slice(0, 60);
  const pickablePlayers = players.slice().sort((a, b) => Number(a.adp) - Number(b.adp));

  function addForcedPick() {
    setForcedPicks((prev) => [...prev, { round: prev.length + 1, playerId: "" }]);
  }

  function updateForcedPick(index: number, field: "round" | "playerId", value: string) {
    setForcedPicks((prev) =>
      prev.map((f, i) =>
        i === index ? { ...f, [field]: field === "round" ? Number(value) : value } : f
      )
    );
  }

  function removeForcedPick(index: number) {
    setForcedPicks((prev) => prev.filter((_, i) => i !== index));
  }

  function slotForPick(pickIndex: number, numTeams: number): number {
    const round = Math.floor(pickIndex / numTeams);
    const posInRound = pickIndex % numTeams;
    return round % 2 === 0 ? posInRound + 1 : numTeams - posInRound;
  }

  function logPick(playerId: string) {
    if (!playerId) return;
    setDraftLog((prev) => (prev.includes(playerId) ? prev : [...prev, playerId]));
    setNextPickPlayer("");
  }

  function logNextPick() {
    logPick(nextPickPlayer);
  }

  function undoLastPick() {
    setDraftLog((prev) => prev.slice(0, -1));
  }

  const nextRound = active ? Math.floor(draftLog.length / active.num_teams) + 1 : 1;
  const nextSlot = active ? slotForPick(draftLog.length, active.num_teams) : 1;
  const draftedIds = new Set(draftLog);
  const boardPickable = pickablePlayers.filter((p) => !draftedIds.has(p.player_id));
  const availableWaivers = waivers.filter((p) => !draftedIds.has(p.player_id));

  const domainMin = results.length ? Math.min(...results.map((r) => r.p10)) : 0;
  const domainMax = results.length ? Math.max(...results.map((r) => r.p90)) : 1;
  const domainSpan = Math.max(1, domainMax - domainMin);

  return (
    <>
      <div className="header">
        <div>
          <h1>FFB Draft Simulator</h1>
          <p>Fantasy football draft planning across your leagues</p>
        </div>
        <button className="help-btn" onClick={() => setShowHelp(true)} title="How the math works">
          ?
        </button>
        <div className="session-bar">
          <span>{user.display_name ?? user.sleeper_username}</span>
          <button className="tab" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </div>

      {showHelp && (
        <div className="modal-backdrop" onClick={() => setShowHelp(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h2>How the math works</h2>
              <button className="remove-pick-btn" onClick={() => setShowHelp(false)}>
                ✕
              </button>
            </div>
            <div className="modal-body">
              <h3>Player value: VORP</h3>
              <p>
                A player's raw projection doesn't say much on its own - what matters is how much
                better they are than the player you'd get for free at the same position. We
                compute replacement level per position: the projection of the last starter-worthy
                player at that position, league-wide, given your roster rules and number of
                teams. Then:
              </p>
              <pre>VORP(player) = player.proj_points - replacement_level[player.position]</pre>
              <p>
                This is what ranks the "Suggested next pick" list and the sample roster's picks.
              </p>

              <h3>Opponent draft model</h3>
              <p>
                Each simulated opponent doesn't just take the top of the ADP board - real drafts
                have noise (reaches, sleepers) and are shaped by roster need. For each available
                player we compute a noisy effective draft slot:
              </p>
              <pre>effective_slot = ADP + Normal(0, sigma) + need_penalty</pre>
              <p>
                The opponent takes the player with the lowest effective slot. Noise grows by
                round, so round 1 is close to chalk and late rounds are noisy reaches. Once a
                position's starting requirement is already filled on a team's roster, that
                position gets a penalty that pushes opponents off it.
              </p>

              <h3>Draft simulation</h3>
              <p>
                One simulated draft plays the snake order pick by pick. Any pick already logged
                on the Live Draft Board is replayed exactly as it happened, not simulated. Your
                own forced picks are reserved ahead of time so they can't be sniped by an
                opponent. Running many of these and looking at the distribution of your team's
                projected points (mean, stdev, P10/P50/P90) is how "Scenario Results" gets built.
              </p>

              <h3>Season simulation: points to wins</h3>
              <p>
                Total projected points is a rough score - what actually matters is win
                probability. Each player gets a season-long luck factor plus a week-to-week luck
                factor on top of it, every team starts its best lineup by projection, and a full
                round-robin schedule is played out to count wins. Two variance-reduction tricks
                (common random numbers and Latin hypercube sampling) keep results stable with
                fewer simulation runs.
              </p>
            </div>
          </div>
        </div>
      )}

      <div className="tabs">
        {leagues.map((l) => (
          <button
            key={l.key}
            className={`tab ${l.key === activeKey ? "active" : ""}`}
            onClick={() => setActiveKey(l.key)}
          >
            {l.name}
          </button>
        ))}
      </div>

      {active && (
        <div className="subtabs">
          <button
            className={`subtab ${view === "draft" ? "active" : ""}`}
            onClick={() => setView("draft")}
          >
            Draft
          </button>
          <button
            className={`subtab ${view === "waivers" ? "active" : ""}`}
            onClick={() => setView("waivers")}
          >
            Waivers
          </button>
          <button
            className={`subtab ${view === "schedule" ? "active" : ""}`}
            onClick={() => setView("schedule")}
          >
            Schedule
          </button>
          <button
            className={`subtab ${view === "sims" ? "active" : ""}`}
            onClick={() => setView("sims")}
          >
            Simulations
          </button>
        </div>
      )}

      {active && view === "draft" && (
        <div className="grid">
          <div className="card full-width">
            <h2>Your Roster</h2>
            {rosterError && <p className="state-msg error">{rosterError}</p>}
            {!rosterError && !roster && <p className="state-msg">Loading...</p>}
            {roster && (
              <>
                <div className="roster-meta">
                  <div>
                    <span>Manager</span>
                    <strong>{roster.display_name}</strong>
                  </div>
                  <div>
                    <span>Teams</span>
                    <strong>{active.num_teams}</strong>
                  </div>
                  <div>
                    <span>Waivers</span>
                    <strong>{active.faab ? "FAAB" : "Rolling"}</strong>
                  </div>
                </div>
                <ul className="player-list">
                  {roster.player_ids.length === 0 && forcedPicks.length === 0 && (
                    <li className="state-msg">No players rostered yet</li>
                  )}
                  {roster.player_ids.map((id) => (
                    <li key={id} className="player-chip">
                      <img
                        className="avatar avatar-sm"
                        src={avatarUrl(id, playerById.get(id)?.position, playerById.get(id)?.name)}
                        onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                        alt=""
                      />
                      {playerById.get(id)?.name ?? id}
                    </li>
                  ))}
                  {forcedPicks
                    .filter((f) => f.playerId)
                    .sort((a, b) => a.round - b.round)
                    .map((f) => (
                      <li key={`plan-${f.round}`} className="player-chip planned">
                        <img
                          className="avatar avatar-sm"
                          src={avatarUrl(f.playerId, playerById.get(f.playerId)?.position, playerById.get(f.playerId)?.name)}
                          onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                          alt=""
                        />
                        R{f.round}: {playerById.get(f.playerId)?.name ?? f.playerId}
                      </li>
                    ))}
                </ul>
              </>
            )}
          </div>

          <div className="card full-width">
            <h2>Live Draft Board</h2>
            <p className="state-msg" style={{ marginBottom: 14 }}>
              Track the real draft here as it happens, pick by pick. Every pick you log here is
              locked in and removed from the simulator.
            </p>
            <div className={`draft-board-status ${nextSlot === mySlot ? "on-the-clock" : ""}`}>
              <span>
                On the clock: <strong>Round {nextRound}</strong>,{" "}
                <strong>{teamNames[nextSlot] ?? `Slot ${nextSlot}`}</strong>
              </span>
              {nextSlot === mySlot && <span className="your-pick-badge">Your pick</span>}
              {draftLog.length > 0 && (
                <button className="remove-pick-btn undo-btn" onClick={undoLastPick} title="Undo last pick">
                  ↺ Undo
                </button>
              )}
            </div>

            {nextSlot === mySlot && recommendations.length > 0 && (
              <div className="your-turn-box">
                <span className="recommend-label">It's your pick, take the best value</span>
                <ol className="rank-list">
                  {recommendations.slice(0, 5).map((r, i) => (
                    <li key={r.player_id} className="rank-row rank-row-clickable" onClick={() => logPick(r.player_id)}>
                      <span className="rank-num">{i + 1}</span>
                      <img
                        className="avatar avatar-sm"
                        src={avatarUrl(r.player_id, r.position, r.name)}
                        onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                        alt=""
                      />
                      <div className="rank-body">
                        <div className="rank-top-line">
                          <span className="rank-name">{r.name}</span>
                          <span className="pos-pill">{r.position}</span>
                          <span className="rank-vorp">+{r.vorp.toFixed(0)} VORP</span>
                        </div>
                        <p className="rank-reason">{r.reason}</p>
                      </div>
                      <button className="draft-pick-btn">Draft</button>
                    </li>
                  ))}
                </ol>
              </div>
            )}

            <details className="any-pick-picker">
              <summary>
                {nextSlot === mySlot ? "Or pick someone else" : "Log this pick"}
              </summary>
              <div className="force-row">
                <select value={nextPickPlayer} onChange={(e) => setNextPickPlayer(e.target.value)}>
                  <option value="">Select who was picked...</option>
                  {boardPickable.map((p) => (
                    <option key={p.player_id} value={p.player_id}>
                      {p.name} ({p.position}, ADP {Number(p.adp).toFixed(1)})
                    </option>
                  ))}
                </select>
                <button className="remove-pick-btn" onClick={logNextPick} title="Log pick">
                  ✓
                </button>
              </div>
            </details>
            {draftLog.length > 0 && (
              <ol className="draft-log">
                {draftLog.map((id, i) => {
                  const slot = slotForPick(i, active.num_teams);
                  return (
                    <li key={i} className={slot === mySlot ? "mine" : ""}>
                      <img
                        className="avatar avatar-sm"
                        src={avatarUrl(id, playerById.get(id)?.position, playerById.get(id)?.name)}
                        onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                        alt=""
                      />
                      <span className="draft-log-pick">
                        R{Math.floor(i / active.num_teams) + 1}.{slot}
                      </span>
                      {playerById.get(id)?.name ?? id}
                      <span className="draft-log-team">{teamNames[slot] ?? `Slot ${slot}`}</span>
                    </li>
                  );
                })}
              </ol>
            )}
          </div>

          <div className="card">
            <h2>Run Draft Simulation</h2>
            <div className="form-row">
              <div className="field">
                <label>My draft slot</label>
                <input
                  type="number"
                  min={1}
                  max={active.num_teams}
                  value={mySlot}
                  onChange={(e) => setMySlot(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>Number of simulations</label>
                <input
                  type="number"
                  min={50}
                  step={50}
                  value={nSims}
                  onChange={(e) => setNSims(Number(e.target.value))}
                />
              </div>
            </div>

            <div className="force-picks">
              <label>What if I take... (hypothetical future picks, doesn't affect the live board)</label>
              {forcedPicks.map((f, i) => (
                <div className="force-row" key={i}>
                  <input
                    type="number"
                    min={1}
                    className="force-round"
                    value={f.round}
                    onChange={(e) => updateForcedPick(i, "round", e.target.value)}
                  />
                  <select
                    value={f.playerId}
                    onChange={(e) => updateForcedPick(i, "playerId", e.target.value)}
                  >
                    <option value="">Select a player...</option>
                    {pickablePlayers.map((p) => (
                      <option key={p.player_id} value={p.player_id}>
                        {p.name} ({p.position}, ADP {Number(p.adp).toFixed(1)})
                      </option>
                    ))}
                  </select>
                  <button className="remove-pick-btn" onClick={() => removeForcedPick(i)}>
                    ✕
                  </button>
                </div>
              ))}
              <button className="add-pick-btn" onClick={addForcedPick}>
                + Add forced pick
              </button>
            </div>

            <button className="run-btn" onClick={runSim} disabled={running}>
              {running ? "Simulating..." : "Run simulation"}
            </button>
            {runError && <p className="state-msg error">{runError}</p>}

            {lastSample.length > 0 && (
              <div style={{ marginTop: 22 }}>
                <h2>Why This Roster</h2>
                <ul className="reason-list">
                  {lastSample.map((p) => (
                    <li key={p.round}>
                      <div className="reason-head">
                        <img
                          className="avatar avatar-sm"
                          src={avatarUrl(p.player_id, p.position, p.name)}
                          onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                          alt=""
                        />
                        <strong>
                          R{p.round}: {p.name}
                        </strong>
                        <span className="pos-pill">{p.position}</span>
                      </div>
                      <p className="reason-text">{p.reason}</p>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div style={{ marginTop: 22 }}>
              <h2>Scenario Results</h2>
              {results.length === 0 && (
                <p className="results-empty">No simulations run yet for this league.</p>
              )}
              {results.length > 0 && (
                <table className="results-table">
                  <thead>
                    <tr>
                      <th>Scenario</th>
                      <th>Mean</th>
                      <th style={{ width: 120 }}>P10 - P90</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.map((r) => (
                      <tr key={r.scenario}>
                        <td className="scenario-name">{r.scenario}</td>
                        <td>{r.mean.toFixed(0)}</td>
                        <td>
                          <div className="range-bar">
                            <div
                              className="fill"
                              style={{
                                left: `${((r.p10 - domainMin) / domainSpan) * 100}%`,
                                width: `${Math.max(4, ((r.p90 - r.p10) / domainSpan) * 100)}%`,
                              }}
                            />
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          <div className="card full-width">
            <h2>Player Pool</h2>
            <div className="pos-filters">
              {positions.map((pos) => (
                <button
                  key={pos}
                  className={`pos-filter ${pos === posFilter ? "active" : ""}`}
                  onClick={() => setPosFilter(pos)}
                >
                  {pos}
                </button>
              ))}
            </div>
            {players.length === 0 && (
              <p className="results-empty">No player pool found for this league yet.</p>
            )}
            {players.length > 0 && (
              <div className="players-scroll">
                <table className="players-table">
                  <thead>
                    <tr>
                      <th></th>
                      <th>Player</th>
                      <th>Pos</th>
                      <th>Team</th>
                      <th>ADP</th>
                      <th>Proj. Pts</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shownPlayers.map((p) => (
                      <tr
                        key={p.player_id}
                        title={`${Number(p.proj_points).toFixed(0)} projected points at ADP ${Number(
                          p.adp
                        ).toFixed(1)} - goes off the board around pick ${Math.round(Number(p.adp))}.`}
                      >
                        <td>
                          <img
                            className="avatar"
                            src={avatarUrl(p.player_id, p.position, p.name)}
                            onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                            alt=""
                          />
                        </td>
                        <td>{p.name}</td>
                        <td>
                          <span className="pos-pill">{p.position}</span>
                        </td>
                        <td>
                          {p.position === "DEF" ? (
                            normalizeTeam(p.player_id)
                          ) : p.nfl_team ? (
                            <span className="team-cell">
                              <img
                                className="team-logo"
                                src={teamLogoUrl(p.nfl_team)}
                                onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                                alt=""
                              />
                              {normalizeTeam(p.nfl_team)}
                            </span>
                          ) : (
                            "-"
                          )}
                        </td>
                        <td>{Number(p.adp).toFixed(1)}</td>
                        <td>{Number(p.proj_points).toFixed(0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {active && view === "waivers" && (
        <div className="grid">
          <div className="card full-width">
            <h2>Recommended Pickups</h2>
            <p className="state-msg" style={{ marginBottom: 14 }}>
              Best available players for your roster, ranked by value over replacement.
              Players marked "need" would fill a starting spot you don't currently have covered.
            </p>
            {waiverRecsLoading && <p className="state-msg">Loading...</p>}
            {!waiverRecsLoading && waiverRecs.length === 0 && (
              <p className="results-empty">No recommendations available.</p>
            )}
            {!waiverRecsLoading && waiverRecs.length > 0 && (
              <ol className="rank-list">
                {waiverRecs.map((r, i) => (
                  <li key={r.player_id} className="rank-row">
                    <span className="rank-num">{i + 1}</span>
                    <img
                      className="avatar avatar-sm"
                      src={avatarUrl(r.player_id, r.position, r.name)}
                      onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                      alt=""
                    />
                    <div className="rank-body">
                      <div className="rank-top-line">
                        <span className="rank-name">{r.name}</span>
                        <span className="pos-pill">{r.position}</span>
                        {r.nfl_team && <span className="pos-pill">{normalizeTeam(r.nfl_team)}</span>}
                        <span className="rank-vorp">+{r.vorp.toFixed(0)} VORP</span>
                        {r.fills_need && <span className="your-pick-badge">Need</span>}
                      </div>
                      <p className="rank-reason">{r.reason}</p>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </div>

          <div className="card full-width">
          <h2>Waiver Wire</h2>
          <p className="state-msg" style={{ marginBottom: 14 }}>
            Players in the pool not currently rostered by anyone in {active.name}, and not already
            taken in your logged live draft.
          </p>
          {waiversLoading && <p className="state-msg">Loading...</p>}
          {!waiversLoading && availableWaivers.length === 0 && (
            <p className="results-empty">No unrostered players found.</p>
          )}
          {!waiversLoading && availableWaivers.length > 0 && (
            <div className="players-scroll">
              <table className="players-table">
                <thead>
                  <tr>
                    <th></th>
                    <th>Player</th>
                    <th>Pos</th>
                    <th>Team</th>
                    <th>ADP</th>
                    <th>Proj. Pts</th>
                  </tr>
                </thead>
                <tbody>
                  {availableWaivers.map((p) => (
                    <tr
                      key={p.player_id}
                      title={`${Number(p.proj_points).toFixed(0)} projected points, unrostered - ADP ${Number(
                        p.adp
                      ).toFixed(1)} suggests they'd normally go earlier than this.`}
                    >
                      <td>
                        <img
                          className="avatar"
                          src={avatarUrl(p.player_id, p.position, p.name)}
                          onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                          alt=""
                        />
                      </td>
                      <td>{p.name}</td>
                      <td>
                        <span className="pos-pill">{p.position}</span>
                      </td>
                      <td>
                        {p.position === "DEF" ? (
                          normalizeTeam(p.player_id)
                        ) : p.nfl_team ? (
                          <span className="team-cell">
                            <img
                              className="team-logo"
                              src={teamLogoUrl(p.nfl_team)}
                              onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                              alt=""
                            />
                            {normalizeTeam(p.nfl_team)}
                          </span>
                        ) : (
                          "-"
                        )}
                      </td>
                      <td>{Number(p.adp).toFixed(1)}</td>
                      <td>{Number(p.proj_points).toFixed(0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          </div>
        </div>
      )}

      {active && view === "schedule" && (
        <div className="card full-width">
          <h2>NFL Schedule</h2>
          <p className="state-msg" style={{ marginBottom: 14 }}>
            Game-by-game schedule for the {active.season} season, useful for spotting bye weeks
            and stacked matchups before you draft.
          </p>
          <div className="pos-filters">
            {weeks.map((w) => (
              <button
                key={w}
                className={`pos-filter ${w === scheduleWeek ? "active" : ""}`}
                onClick={() => setScheduleWeek(w)}
              >
                Wk {w}
              </button>
            ))}
          </div>
          {scheduleLoading && <p className="state-msg">Loading...</p>}
          {!scheduleLoading && games.length === 0 && (
            <p className="results-empty">No games found for this week.</p>
          )}
          {!scheduleLoading && games.length > 0 && (
            <ul className="schedule-list">
              {games.map((g) => (
                <li key={g.game_id} className="schedule-row">
                  <span className="schedule-date">
                    {g.weekday}, {g.gameday}
                    {g.gametime ? ` · ${g.gametime} ET` : ""}
                  </span>
                  <span className="schedule-matchup">
                    <img
                      className="team-logo"
                      src={teamLogoUrl(g.away_team)}
                      onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                      alt=""
                    />
                    <strong>{normalizeTeam(g.away_team)}</strong>
                    <span className="schedule-at">@</span>
                    <img
                      className="team-logo"
                      src={teamLogoUrl(g.home_team)}
                      onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                      alt=""
                    />
                    <strong>{normalizeTeam(g.home_team)}</strong>
                  </span>
                  {g.away_score !== null && g.home_score !== null && (
                    <span className="schedule-score">
                      {g.away_score} - {g.home_score}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {active && view === "sims" && (
        <div className="grid">
          <div className="card full-width">
            <h2>Season Simulation</h2>
            <p className="state-msg" style={{ marginBottom: 14 }}>
              Simulates the rest of the draft, then plays a full season of head-to-head matchups:
              projected points become wins. Your logged live picks and any forced picks from the
              Draft tab are carried over.
            </p>
            <div className="form-row">
              <div className="field">
                <label>My draft slot</label>
                <input
                  type="number"
                  min={1}
                  max={active.num_teams}
                  value={mySlot}
                  onChange={(e) => setMySlot(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>Simulated seasons</label>
                <input
                  type="number"
                  min={50}
                  max={2000}
                  step={50}
                  value={nSamples}
                  onChange={(e) => setNSamples(Number(e.target.value))}
                />
              </div>
            </div>
            <button className="run-btn" onClick={runSeasonSim} disabled={seasonRunning}>
              {seasonRunning ? "Simulating seasons..." : "Run season simulation"}
            </button>
            {seasonError && <p className="state-msg error">{seasonError}</p>}
            {!seasonSim && !seasonRunning && !seasonError && (
              <p className="results-empty">No season simulation run yet for this league.</p>
            )}
          </div>

          {seasonSim && (
            <>
              <div className="card full-width">
                <h2>Expected Season</h2>
                <div className="stat-tiles">
                  {seasonSim.scenarios.map((s, i) => (
                    <div className="stat-tile" key={s.scenario}>
                      <span className="stat-tile-name">
                        <span
                          className="series-swatch"
                          style={{ background: SERIES[i % SERIES.length] }}
                        />
                        {scenarioLabel(s)}
                      </span>
                      <strong className="stat-hero">{s.exp_wins.toFixed(2)}</strong>
                      <span className="stat-tile-unit">
                        expected wins in {seasonSim.reg_season_weeks} weeks, spread ±{" "}
                        {s.win_stdev.toFixed(2)} across seasons
                      </span>
                      <div className="stat-tile-row">
                        <span>{s.threshold_wins}+ win seasons</span>
                        <strong>{s.threshold_pct.toFixed(1)}%</strong>
                      </div>
                      <div className="stat-tile-row">
                        <span>Average points for</span>
                        <strong>{s.avg_points.toFixed(0)}</strong>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="card full-width">
                <h2>Win Distribution</h2>
                <p className="state-msg" style={{ marginBottom: 10 }}>
                  Share of {seasonSim.n_samples} simulated seasons ending on each win total, over{" "}
                  {seasonSim.rounds} drafted rounds.
                </p>
                {seasonSim.scenarios.length > 1 && (
                  <div className="chart-legend">
                    {seasonSim.scenarios.map((s, i) => (
                      <span key={s.scenario} className="chart-legend-item">
                        <span
                          className="series-swatch"
                          style={{ background: SERIES[i % SERIES.length] }}
                        />
                        {scenarioLabel(s)}
                      </span>
                    ))}
                  </div>
                )}
                <WinDistributionChart scenarios={seasonSim.scenarios} />
                <p className="chart-caption">Regular-season wins</p>
              </div>

              <div className="card full-width">
                <h2>Season Points Range</h2>
                <table className="results-table">
                  <thead>
                    <tr>
                      <th>Scenario</th>
                      <th>Exp. wins</th>
                      <th>{seasonSim.scenarios[0].threshold_wins}+ wins</th>
                      <th>Avg pts</th>
                      <th style={{ width: 220 }}>P10 - P90 points</th>
                    </tr>
                  </thead>
                  <tbody>
                    {seasonSim.scenarios.map((s, i) => {
                      const lo = Math.min(...seasonSim.scenarios.map((x) => x.points_p10));
                      const hi = Math.max(...seasonSim.scenarios.map((x) => x.points_p90));
                      const span = Math.max(1, hi - lo);
                      return (
                        <tr key={s.scenario}>
                          <td className="scenario-name">{scenarioLabel(s)}</td>
                          <td>{s.exp_wins.toFixed(2)}</td>
                          <td>{s.threshold_pct.toFixed(1)}%</td>
                          <td>{s.avg_points.toFixed(0)}</td>
                          <td>
                            <div
                              className="range-bar"
                              title={`P10 ${s.points_p10.toFixed(0)}, median ${s.points_p50.toFixed(
                                0
                              )}, P90 ${s.points_p90.toFixed(0)} points`}
                            >
                              <div
                                className="fill"
                                style={{
                                  left: `${((s.points_p10 - lo) / span) * 100}%`,
                                  width: `${Math.max(4, ((s.points_p90 - s.points_p10) / span) * 100)}%`,
                                  background: SERIES[i % SERIES.length],
                                }}
                              />
                              <div
                                className="median-tick"
                                style={{ left: `${((s.points_p50 - lo) / span) * 100}%` }}
                              />
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}

      {!active && leagues.length === 0 && (
        <p className="state-msg">
          Can't reach the API. Start it with <code>uv run uvicorn ffb.api:app --reload</code> in{" "}
          <code>backend/</code>.
        </p>
      )}
    </>
  );
}

export default App;
