"""Thin read-only client for the Sleeper API (https://docs.sleeper.com/)."""

import httpx

BASE_URL = "https://api.sleeper.app/v1"


class SleeperClient:
    def __init__(self) -> None:
        self._client = httpx.Client(base_url=BASE_URL, timeout=15.0)

    def get_user(self, username: str) -> dict:
        return self._get(f"/user/{username}")

    def get_user_leagues(self, user_id: str, season: str, sport: str = "nfl") -> list[dict]:
        return self._get(f"/user/{user_id}/leagues/{sport}/{season}")

    def get_league(self, league_id: str) -> dict:
        return self._get(f"/league/{league_id}")

    def get_rosters(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/rosters")

    def get_users(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/users")

    def get_matchups(self, league_id: str, week: int) -> list[dict]:
        return self._get(f"/league/{league_id}/matchups/{week}")

    def get_transactions(self, league_id: str, week: int) -> list[dict]:
        return self._get(f"/league/{league_id}/transactions/{week}")

    def get_draft(self, draft_id: str) -> dict:
        return self._get(f"/draft/{draft_id}")

    def get_draft_picks(self, draft_id: str) -> list[dict]:
        return self._get(f"/draft/{draft_id}/picks")

    def get_players(self, sport: str = "nfl") -> dict:
        # Large payload (~5MB) - Sleeper asks this be cached, not polled repeatedly.
        return self._get(f"/players/{sport}")

    def get_trending_players(
        self, sport: str = "nfl", trend_type: str = "add", lookback_hours: int = 24, limit: int = 25
    ) -> list[dict]:
        return self._get(
            f"/players/{sport}/trending/{trend_type}",
            params={"lookback_hours": lookback_hours, "limit": limit},
        )

    def _get(self, path: str, params: dict | None = None):
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SleeperClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
