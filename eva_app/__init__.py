"""Eva's application wiring, one module per feature (release plan, Phase 1: split main.py).

main.py is the composition root: it builds the shared context (context.Ctx) and calls each
feature's install(ctx) in a fixed, visible order. A feature reads what earlier ones put on ctx
and adds what later ones need. Thread rule as before: Qt widgets only on the GUI thread
(ctx.bridge.run_sync / ctx.bridge.fire); native desktop automation never on it.
"""
