# Life programme -- status

Design: docs/life/LIFE_DESIGN.md. One line per finished phase.

- Phase 1 built -- 2026-11-14 -- senses and attention: world_sensor.py samples cursor/window/typing/idle/full-screen/new-window at 8 Hz (read-only), avatar_ui.set_world sends it to the page, and the page's attention system picks a decaying target with hysteresis driving eyes -> head -> torso; focus mode (fast typing, full screen, call) keeps her still with at most one glance in ten seconds; new config flags EVA_WORLD/EVA_ATTENTION/EVA_FOCUS_MODE. Also fixed coder.safe_folder refusing %TEMP% jobs (Windows keeps TEMP under AppData), which failed an existing check. -- tests: node tools/motion_test.js all 14 pass; run_checks.py All checks passed. -- see docs/life/phase1-plan.md
- Phase 1 approved by the manager (Claude) -- 2026-11-14 -- review fixes: Win32 rects converted to Qt logical
  pixels (they were mixed with the cursor's at 125 % scaling), and "full screen" now means the whole monitor
  including the taskbar (a maximised window had counted, which would have kept her in focus mode all day).
- Phase 3 built -- 2026-11-14 -- body that moves like a person: body-life layer in avatar_3d.html (energy-scaled breathing, weight shift with planted feet, head-leads/torso-follows overlap, anticipation and overshoot-settle, Perlin micro-motion, walk sway and rounded stride, spring-turn) plus cloth driving the model's own spring bones (walk inertia and breeze), flags EVA_BODY_LIFE/EVA_CLOTH_PHYSICS via body_life.py -- tests: node tools/motion_test.js all 29 pass; run_checks.py All checks passed. -- awaiting manager review
