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

    loadResults(activeKey);
  }, [activeKey]);

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
          forced_picks: {},
        }),
      });
      if (!res.ok) throw new Error("Simulation failed to run");
      loadResults(activeKey);
    } catch (e) {
      setRunError(e instanceof Error ? e.message : "Simulation failed to run");
    } finally {
      setRunning(false);
    }
  }

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
        <div className="grid">
          <div className="card">
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
                  {roster.player_ids.length === 0 && (
                    <li className="state-msg">No players rostered yet</li>
                  )}
                  {roster.player_ids.map((id) => (
                    <li key={id} className="player-chip">
                      {id}
                    </li>
                  ))}
                </ul>
              </>
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
            <button className="run-btn" onClick={runSim} disabled={running}>
              {running ? "Simulating..." : "Run baseline simulation"}
            </button>
            {runError && <p className="state-msg error">{runError}</p>}

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
