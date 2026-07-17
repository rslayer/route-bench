# e2e

Playwright, driving the built app.

```bash
npm run build && npx next start -p 3111 &
npm test
```

## What is real and what is mocked

The **app is real** — built and served, not stubbed. The **API is mocked**, for
two reasons: a real analysis takes minutes and needs OSRM and an LLM key, and the
server's own behaviour already has far better coverage in the Python suite,
against the real validator and the real models.

So these tests own the client's half of the contract: that a messy file becomes a
standard one, that the panel state becomes the config JSON verbatim, that the
artifact's features reach the map, and that each API error code gets its own
honest screen.

**The fixtures mirror the real artifacts.** If a contract moves, the Python side
fails first and loudly — these fixtures are not the source of truth, and should
be corrected to match rather than the other way round.

## The map

MapLibre needs a WebGL context and network vector tiles; CI reliably has neither.
The tile source is stubbed to an empty-but-valid style and the assertions are on
source and layer state — did the right features reach the map — which is what
actually matters. Pixels are not tested; a screenshot diff here would fail on
font rendering and tell us nothing about whether the routes are right.
