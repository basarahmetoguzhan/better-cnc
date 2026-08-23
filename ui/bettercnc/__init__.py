"""better-cnc operator UI.

Built for the MARBLE machine; the test rig is a subset of it. Capabilities the
current machine lacks are rendered disabled with the reason on screen, never
hidden - see capabilities.py for why that is structural rather than a convention.

Layers, outermost first:

    app.py         stdlib HTTP server
    render.py      view model -> HTML (server-side, so it is testable)
    viewmodel.py   (profile, config, state) -> plain dicts. Pure.
    adapter.py     the LinuxCNC boundary. Mock and real.
    state.py       machine state shapes: estop, homing, link health
    configmodel.py axis/timing values, read from the machine's INI
    profiles.py    test-rig and marble declarations
    capabilities.py the capability catalogue
    iniload.py     INI parsing that keeps track of what was never measured

No hardware number is hardcoded above configmodel.py: everything traces to
configs/*/**.ini or to a docs/ citation recorded in profiles.py.
"""

__version__ = "0.1.0"
