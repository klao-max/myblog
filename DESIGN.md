# Design system

## Visual world

Night Desk / Warm Paper. The admin UI is a dark, high-contrast editor's desk: precise, compact, and calm. The public UI is a warm paper surface with ink-black type and orange/cyan editorial marks. The same spacing rhythm, corner radius, and typography system connects both surfaces.

## Modes

- Public: Read + Experience. Articles are the main reading surface; projects and games are presented as work, not dashboard widgets.
- Admin: Operate. The owner should see state, next action, and content health immediately.

## Tokens

```css
--ink: #11161d;
--ink-soft: #5d6875;
--paper: #f3eee5;
--paper-deep: #e7dfd3;
--desk: #0b1119;
--desk-panel: #121b26;
--desk-line: #263446;
--desk-muted: #9baabc;
--orange: #ff8a3d;
--cyan: #63d9c7;
--red: #e36b6b;
--radius: 14px;
--wrap: 1180px;
```

## Signature interaction

The homepage presents three making lanes—writing, projects, games—with a quiet horizontal rule that becomes orange on hover. Admin lists use ledger rows: title, status, updated time, and one explicit action. No scroll-triggered animation; hover and focus provide feedback.

## Components

- `site-header`: compact, text-first navigation.
- `content-card`: a bordered editorial unit with one clear action.
- `status-pill`: text plus color, never color alone.
- `admin-shell`: side rail and main work area.
- `editor-form`: two-column desktop form, one-column mobile form.
- `game-frame`: playable game surface with a direct launch control.

## Responsive rules

- 1180px content cap with equal edges.
- 820px collapses the public grid and admin rail.
- 560px uses one column, full-width actions, and a compact header.

## Anti-goals

- No generic SaaS dashboard cards on the public home.
- No decorative gradients, fake metrics, or invented social proof.
- No hidden content behind entrance animations.
