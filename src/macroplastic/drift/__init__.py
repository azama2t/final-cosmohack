"""Drift: demonstration 72 h drift forecast of detected debris patches (OpenDrift OceanDrift).

forcing.py - download a small forcing window (HYCOM ESPC-D-V02 surface currents, NCEP GFS 10 m wind via
             PacIOOS; fallback Open-Meteo or constant fields) into local CF NetCDF files.
run.py     - seed particles around MDD detections, run OceanDrift, write drift.json (docs/CONTRACTS.md).
"""
