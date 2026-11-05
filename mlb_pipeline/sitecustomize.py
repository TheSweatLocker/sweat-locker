"""Auto-load sb_observer for every python run started in mlb_pipeline/.

CPython imports `sitecustomize` during startup, searching sys.path, which
includes the script's own directory. Every pipeline step runs as
`python <step>.py` from this directory, so this is how the observer reaches
all 175 steps and all 386 swallow sites without editing one of them.

Kept to an import and a guard: anything heavier here runs before every
script in the repo and would be impossible to debug.
"""
try:
    import sb_observer  # noqa: F401  (installs on import)
except Exception:
    pass
