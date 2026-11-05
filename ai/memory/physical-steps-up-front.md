---
name: physical-steps-up-front
description: "When asking the user to do something with the hardware, put the action steps first, short and numbered"
metadata:
  node_type: memory
  type: feedback
  originSessionId: <uuid>
  modified: 2026-10-11T12:40:26.961Z
---

When the user has to do something physically (press buttons, tap the screen, reseat a board),
put those steps at the very top of the message as a short numbered list, before any explanation.

**Why:** the user is often distracted and missed a "press the buttons first" step buried mid-message
during touch calibration (2026-10-11), so a whole test run had to be repeated.

**How to apply:** action steps first, explanation after; one action per line; say when to reply ("напиши «готово»").
Background test scripts should tolerate missed steps (timeouts, skip and continue).
