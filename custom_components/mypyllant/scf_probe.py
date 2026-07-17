"""TEMPORARY one-shot no-op probe to find the circuitTimePeriods (WW-Ladezeiten) write
endpoint. Runs ONCE per HA process, bundles ALL candidates into one run, and writes back the
CURRENT plan (true no-op) so nothing changes. Logs status per candidate at WARNING with the
marker SCF-PROBE. Remove this module and its call in coordinator.py after the endpoint is found.
"""

from __future__ import annotations

import logging

from myPyllant.const import SYSTEM_CONTROL_API_URL_BASE

_LOGGER = logging.getLogger(__name__)

_done: set[str] = set()

# All under system-control/v1/systems/{id}/... — segment ends on -time-periods per prior findings.
_CANDIDATES = [
    "domestic-hot-water/{i}/time-periods",
    "domestic-hot-water/{i}/circuit-time-periods",
    "domestic-hot-water/{i}/loading-time-periods",
    "domestic-hot-water/{i}/dhw-time-periods",
]


def _noop_body(plan: dict) -> dict:
    """Current plan → API body, values unchanged (true no-op). Day keys lowercased."""
    out: dict[str, list[dict]] = {}
    for day, slots in (plan or {}).items():
        out[str(day).lower()] = [
            {
                "startTime": s.get("startTime"),
                "endTime": s.get("endTime"),
                "setpoint": s.get("setpoint"),
            }
            for s in (slots or [])
        ]
    return out


async def probe_circuit_time_periods(api, system_id: str, points) -> None:
    if system_id in _done:
        return
    _done.add(system_id)
    point = next(
        (p for p in points if p.path and p.path[-1] == "circuitTimePeriods"), None
    )
    if not point:
        _LOGGER.warning("SCF-PROBE %s: kein circuitTimePeriods-Punkt gefunden", system_id)
        return
    index = point.path[1]
    body = _noop_body(point.value)
    _LOGGER.warning("SCF-PROBE %s: teste %d Kandidaten (No-Op)", system_id, len(_CANDIDATES))
    for tmpl in _CANDIDATES:
        suffix = tmpl.format(i=index)
        url = f"{SYSTEM_CONTROL_API_URL_BASE}/systems/{system_id}/{suffix}"
        try:
            async with api.aiohttp_session.patch(
                url, json=body, headers=api.get_authorized_headers()
            ) as resp:
                _LOGGER.warning("SCF-PROBE %s -> %s", suffix, resp.status)
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("SCF-PROBE %s -> ERR %s", suffix, e)
