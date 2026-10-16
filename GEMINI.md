# Project Guidelines: SIM Inventory & SMS Dashboard (D:\panel)

Project rules and active engineering standards for Antigravity & AI coding agents working on this workspace.

---

## 1. Project Context & Invariant Boundaries

This workspace builds a Windows application managing a 16–64 port modem pool (Foxcom 32-port) for manual SIM swaps, SMS processing, and a local web dashboard.
The single source of truth for specifications is [`agent-spec-sim-inventory.md`](agent-spec-sim-inventory.md).

### Invariants (Strictly Enforced)
* **I-1 to I-13**: Follow all invariants defined in `agent-spec-sim-inventory.md` section 2 without exception.
* **Authority Limits (Section 3)**:
  * **Allowed without asking**: Write code, run tests against simulated modems (mock serial), write documentation.
  * **MUST ASK human before**: Opening real serial ports, altering modem configuration/NV, sending AT commands outside allowlist, deleting DB/SMS data, or adding dependencies with new licenses.

---

## 2. Ponytail (Lazy Senior Dev Mode) — Active

Always apply the Ponytail philosophy: **the best code is the code never written. Solve the problem with the smallest complete change.**

1. **Simplicity Ladder**:
   - Does this need to exist? Skip unrequested features, options, and abstractions.
   - Already in the codebase? Reuse existing helpers and patterns.
   - Standard library first (Python standard library / PySerial). Avoid adding dependencies for trivial tasks.
   - Shortest working diff wins.
2. **Quality & Discipline**:
   - Be lazy about the solution, never about completeness: handle callers, errors, and tests that your change impacts.
   - Do not remove validation at trust boundaries or safety checks.
   - Mark intentional shortcuts with: `shortcut: <the limit>, <when to upgrade>`.
3. **Turn Summary**:
   - End task responses with 1–2 lines stating what was skipped/unchecked and any known risks.

---

## 3. Anti-Slop Filter — Active

Enforce clean, human-crafted design and code hygiene across all UI, copy, and code:

* **Code Comments Hygiene**:
  - Never write generic AI comments that restate the code (e.g. `// initialize variable`, `# loop through items`).
  - Only write comments that explain the **why** that the code cannot express itself.
* **UI & Dashboard (Indonesian Language)**:
  - Clean, functional local dashboard without generic AI tropes (no fake stats, no decorative fluff).
  - Proper WCAG contrast ratios, responsive layout, clear error states, and keyboard accessibility.
  - Follow Indonesian interface terms as outlined in the spec (`BELUM DIKETAHUI`, `PERLU DITINJAU`, `PESAN BAWAAN`).
