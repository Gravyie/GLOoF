# Experimental (not wired into the app)

`hydrology.py` is an unfinished screening-level flood-extent model (LAHARZ-style:
flow path + cross-section stage + run-out). `test_hydrology.py` is its test and
is currently failing (a pyflwdir call at hydrology.py:147). Needs the fix, a
DEM provider (Copernicus GLO-30 via Earth Search) and a population source before
it can be exposed through the API.
