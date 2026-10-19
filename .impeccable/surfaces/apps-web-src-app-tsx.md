---
version: 1
slug: "apps-web-src-app-tsx"
primary_target: "apps/web/src/App.tsx"
related_targets: ["apps/web/src/views/ResultReport.tsx","apps/web/src/views/StartComparison.tsx"]
---

# MetricGuard web app (all working screens)

Mode: Operate. Audience: data analysts explaining why two numbers for one metric disagree, at a desk in daylight.

## Direction contract

THESIS: Every result is sealed evidence with a case number. The app refuses the category default (navy sidebar, blue buttons, eyebrow labels over paragraphs of explanation over cards) in favour of a case file that leads with the answer.

OWN-WORLD: Charcoal locker rail (#22262A) on a label-stock ground (#F2F1EC) with white panels. Seal red (#C3242B) only for the seal, the primary action and the active marker, never for state. Kraft (#B48A57) for tags and the case-number kicker. Ballpoint blue (#2F4BA0) for links. Verified green and amber are state only. Archivo for the UI, Archivo Narrow tracked caps for labels, JetBrains Mono for every figure, hash and case number.

STORY: The analyst opens Cases and sees what needs a conclusion. They pick data, define two calculations, and read one sentence and one figure that answer the question. The seal shows the evidence is frozen and replayable; they save a conclusion and replay it later.

FIRST VIEWPORT: Left rail with Cases, New comparison, Datasets, Metrics and Changes, plus the workspace and engine status. The result shows a case header (kicker "Case MG-XXXX · dataset · records", the title, current vs proposed) with the seal card on the right. The answer figure is 64px mono, then one sentence. The tabs (Records, Breakdown, Definitions) sit below with the record table. Primary action: "Save conclusion", seal red, top right.

FORM: Chain of Custody, position 5 on the ordered list (forensic evidence handling). seed:90901d5affd1ede3. Raises: case numbers everywhere (Factory Catalog); nothing disappears, it is stamped (Ticket Wallet); the answer owns the result's first viewport (Vertical Feed); one committed hue per area (Acetate Manual); scale contrast between figures and labels (Type Specimen). Signature move: the evidence seal, a perforated seal-red strip carrying the case number, hashes and replay status, turning "Seal intact" green when a replay matches.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Scope and constraints

- Every existing feature keeps working; routes stay addressable (old /dashboard and /investigations links resolve to Cases).
- Exact values, units and limitations stay visible wherever a number appears.
- Usable at 375 px with no horizontal page scroll.

## Unresolved

- Dark mode is not in scope for this pass.

REVISION (2026-10-19, user request): seal blue (#2557D6) replaces seal red everywhere the contract says "seal red"; Light, Dark and System themes added; a guard-shield logo replaces the "MG" ring mark.
