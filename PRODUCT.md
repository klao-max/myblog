# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

delegated: Python standard-library web server, SQLite, server-rendered HTML/CSS/JS, Docker Desktop deployment

## Users

- The owner uses the private admin area to publish writing, maintain a project portfolio, and add small browser games.
- Visitors read articles, inspect projects, open GitHub links, and play games without creating an account.

## Product Purpose

This is a personal publishing home for writing, software projects, and playable experiments. Success means the owner can publish or update each content type from a browser, and a visitor can understand the work and reach it within a few seconds.

## Positioning

The site treats posts, projects, and games as three equal forms of making. The public surface reads like a quiet notebook, while the private surface behaves like a focused editorial desk.

## Operating Context

- One owner signs in to `/admin`.
- Content is stored locally in SQLite and survives container restarts through a mounted `/data` volume.
- Each game is a self-contained static bundle under `games/<slug>/index.html` and is launched from the game detail page.
- Projects keep a human-maintained GitHub URL and optional live demo URL.

## Capabilities and Constraints

- Posts support drafts, publishing, tags, excerpts, Markdown-like body text, and cover URLs.
- Projects support status, technology tags, GitHub URL, demo URL, and featured placement.
- Games support metadata, controls, source URL, featured placement, and a static playable bundle.
- First version has one administrator account and SQLite; multi-user roles, comments, and remote CMS sync are open decisions.
- The app must run locally and in Docker Desktop without a required external database service.

## Brand Commitments

- Visual direction: a dark editorial desk for administration and a warm paper reading surface for public content.
- Tone: direct, personal, calm, specific.

## Evidence on Hand

- No existing brand assets, logo, or content archive were provided.
- Initial demo content and one small game are included as clearly replaceable seed content.

## Product Principles

- Publishing should take fewer steps than browsing the site.
- Each content type gets its own metadata and workflow.
- A project should always lead to its source or demo.
- A game should launch directly and keep its runtime isolated from the site shell.
- Empty, draft, and error states should explain the next action.

## Accessibility & Inclusion

- Keyboard focus must remain visible.
- Forms use explicit labels and readable error messages.
- Color is not the only signal for status.
- Public text uses a readable measure and responsive single-column layout on small screens.
