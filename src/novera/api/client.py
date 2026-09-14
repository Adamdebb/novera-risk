"""Client used by the UI. Local mode calls the service in-process; HTTP mode calls the API.
Both expose the same methods, so screens never know which one they talk to."""

from __future__ import annotations

from typing import Any, Protocol

from novera.api.service import RiskService
from novera.storage.duckdb_repository import DuckDBRepository


class RiskClient(Protocol):
    def runs(self, limit: int = 20) -> list[dict[str, Any]]: ...
    def summary(self, run_id: str | None = None) -> dict[str, Any]: ...
    def positions(
        self, run_id: str | None = None, by: str = "desk_id", **filters: str
    ) -> list[dict[str, Any]]: ...
    def var_by(
        self, by: str, run_id: str | None = None, method: str = "historical_full_revaluation", **filters: str
    ) -> list[dict[str, Any]]: ...
    def var_summary(self, run_id: str | None = None) -> list[dict[str, Any]]: ...
    def var_scenarios(self, run_id: str | None = None) -> list[dict[str, Any]]: ...
    def sensitivities(
        self, run_id: str | None = None, measure: str = "DV01", by: str = "desk_id", **filters: str
    ) -> list[dict[str, Any]]: ...
    def stress(
        self, run_id: str | None = None, by: str | None = "asset_class", **filters: str
    ) -> list[dict]: ...
    def limits(
        self, run_id: str | None = None, status: str | None = None, level: str | None = None
    ) -> list[dict]: ...
    def dq(self, run_id: str | None = None) -> list[dict[str, Any]]: ...
    def pnl(self, run_id: str | None = None, by: str | None = None, **filters: str) -> dict[str, Any]: ...
    def trade(self, trade_id: str, run_id: str | None = None) -> dict[str, Any]: ...
    def audit(self, subject: str | None = None, limit: int = 100) -> list[dict[str, Any]]: ...
    def organisation(self) -> dict[str, Any]: ...


class LocalClient:
    """Opens a read-only connection per call so it coexists with a writer (the EOD run)."""

    def __init__(self, db_path) -> None:
        self.db_path = db_path

    def _call(self, name: str, *a: Any, **kw: Any) -> Any:
        with DuckDBRepository(self.db_path, read_only=True) as repo:
            return getattr(RiskService(repo), name)(*a, **kw)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **kw: self._call(name, *a, **kw)


class HttpClient:
    def __init__(self, base_url: str) -> None:
        import httpx

        self.base = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base, timeout=30)

    def _get(self, path: str, **params: Any) -> Any:
        r = self.http.get(path, params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.json()

    def _rid(self, run_id: str | None) -> str:
        return run_id or "latest"

    def runs(self, limit: int = 20):
        return self._get("/runs", limit=limit)

    def summary(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/summary")

    def positions(self, run_id=None, by="desk_id", **f):
        return self._get(f"/runs/{self._rid(run_id)}/positions", by=by, **f)

    def var_by(self, by, run_id=None, method="historical_full_revaluation", **f):
        return self._get(f"/runs/{self._rid(run_id)}/var", by=by, method=method, **f)

    def var_summary(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/var/summary")

    def var_scenarios(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/var/scenarios")

    def sensitivities(self, run_id=None, measure="DV01", by="desk_id", **f):
        return self._get(f"/runs/{self._rid(run_id)}/sensitivities", measure=measure, by=by, **f)

    def stress(self, run_id=None, by="asset_class", **f):
        return self._get(f"/runs/{self._rid(run_id)}/stress", by=by, **f)

    def limits(self, run_id=None, status=None, level=None):
        return self._get(f"/runs/{self._rid(run_id)}/limits", status=status, level=level)

    def dq(self, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/dq")

    def pnl(self, run_id=None, by=None, **f):
        return self._get(f"/runs/{self._rid(run_id)}/pnl", by=by, **f)

    def trade(self, trade_id, run_id=None):
        return self._get(f"/runs/{self._rid(run_id)}/trades/{trade_id}")

    def audit(self, subject=None, limit=100):
        return self._get("/audit", subject=subject, limit=limit)

    def organisation(self):
        return self._get("/organisation")


def make_client(settings) -> RiskClient:
    import os

    url = os.environ.get("NOVERA_API_URL")
    return HttpClient(url) if url else LocalClient(settings.db_path)  # type: ignore[return-value]
