import { useEffect, useState } from "react";
import "./App.css";

const API = "http://localhost:8010/api";

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
};

type Recommendation = {
  player_id: string;
  name: string;
  position: string;
  proj_points: number;
  vorp: number;
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

function App() {
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
  const [view, setView] = useState<"draft" | "waivers" | "schedule">("draft");
  const [waivers, setWaivers] = useState<Player[]>([]);
  const [waiversLoading, setWaiversLoading] = useState(false);
  const [weeks, setWeeks] = useState<number[]>([]);
  const [scheduleWeek, setScheduleWeek] = useState<number>(1);
  const [games, setGames] = useState<Game[]>([]);
  const [scheduleLoading, setScheduleLoading] = useState(false);

  const active = leagues.find((l) => l.key === activeKey) ?? null;

  useEffect(() => {
    fetch(`${API}/leagues`)
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
    fetch(`${API}/leagues/${activeKey}/roster`)
      .then((r) => {
        if (!r.ok) throw new Error("Couldn't reach Sleeper for this league");
        return r.json();
      })
      .then(setRoster)
      .catch((e) => setRosterError(e.message));

    setPosFilter("ALL");
    fetch(`${API}/leagues/${activeKey}/players`)
      .then((r) => r.json())
      .then(setPlayers)
      .catch(() => setPlayers([]));

    setForcedPicks([]);
    setDraftLog([]);
    setWaivers([]);
    setLastSample([]);
    fetch(`${API}/leagues/${activeKey}/draft-order`)
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
    fetch(`${API}/leagues/${activeKey}/recommend?exclude=${excluded.join(",")}`)
      .then((r) => r.json())
      .then(setRecommendations)
      .catch(() => setRecommendations([]));
  }, [activeKey, forcedPicks, draftLog]);

  useEffect(() => {
    if (!activeKey || view !== "waivers") return;
    setWaiversLoading(true);
    fetch(`${API}/leagues/${activeKey}/waivers`)
      .then((r) => r.json())
      .then(setWaivers)
      .catch(() => setWaivers([]))
      .finally(() => setWaiversLoading(false));
  }, [activeKey, view]);

  useEffect(() => {
    if (!activeKey || view !== "schedule") return;
    fetch(`${API}/leagues/${activeKey}/schedule/weeks`)
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
    fetch(`${API}/leagues/${activeKey}/schedule/${scheduleWeek}`)
      .then((r) => r.json())
      .then(setGames)
      .catch(() => setGames([]))
      .finally(() => setScheduleLoading(false));
  }, [activeKey, view, scheduleWeek]);

  function loadResults(key: string) {
    fetch(`${API}/leagues/${key}/results`)
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
      </div>

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
                        src={`https://sleepercdn.com/content/nfl/players/${id}.jpg`}
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
                          src={`https://sleepercdn.com/content/nfl/players/${f.playerId}.jpg`}
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
                        src={`https://sleepercdn.com/content/nfl/players/${r.player_id}.jpg`}
                        onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                        alt=""
                      />
                      <div className="rank-body">
                        <div className="rank-top-line">
                          <span className="rank-name">{r.name}</span>
                          <span className="pos-pill">{r.position}</span>
                          <span className="rank-vorp">+{r.vorp.toFixed(0)} VORP</span>
                        </div>
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
                        src={`https://sleepercdn.com/content/nfl/players/${id}.jpg`}
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
                          src={`https://sleepercdn.com/content/nfl/players/${p.player_id}.jpg`}
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
                            src={`https://sleepercdn.com/content/nfl/players/${p.player_id}.jpg`}
                            onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                            alt=""
                          />
                        </td>
                        <td>{p.name}</td>
                        <td>
                          <span className="pos-pill">{p.position}</span>
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
                          src={`https://sleepercdn.com/content/nfl/players/${p.player_id}.jpg`}
                          onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                          alt=""
                        />
                      </td>
                      <td>{p.name}</td>
                      <td>
                        <span className="pos-pill">{p.position}</span>
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
                    {g.weekday}
                    {g.gametime ? ` · ${g.gametime}` : ""}
                  </span>
                  <span className="schedule-matchup">
                    <strong>{g.away_team}</strong> @ <strong>{g.home_team}</strong>
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
