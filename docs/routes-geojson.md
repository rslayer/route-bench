# `routes.geojson` — the map contract

The artifact the web UI renders. Written per session to
`sessions/{id}/routes.geojson`, served by `GET /sessions/{id}/routes.geojson`.

The UI renders geography; it never computes it. Everything the map needs is
here.

## ⚠️ Geometry quality — the UI must surface it

Route lines come from OSRM `/route` (real road paths) where available, and
degrade to **straight segments between stops** where not — OSRM unreachable,
slow, or refusing the request. `properties.geometry_quality` is `"exact"` or
`"approximate"`, and **each line feature carries its own**: one unroutable route
falls back alone rather than dragging the fleet down, so a fleet can be mixed.
The collection reports `"exact"` only when every line is exact.

The trap under `"approximate"`: **the distances and times beside the map are
always real road figures** from the matrix, whatever the line looks like. So a
user can see "12.5 miles" next to a straight line that plainly is not 12.5 miles
of road. That is why the flag has to reach the UI rather than sit in the file.

## Shape

```jsonc
{
  "type": "FeatureCollection",
  "bbox": [west, south, east, north],   // absent for an empty fleet
  "properties": {
    "schema_version": 1,
    "geometry_quality": "exact",        // or "approximate"; see above
    "geometry_note": "Route lines follow OSRM road geometry.",
    "has_benchmark": true,              // per-route benchmark ran
    "has_fleet_benchmark": false,       // fleet VRPTW ran (skipped for 1-route,
                                        //   multi-depot, or >300-stop fleets)
    "route_count": 6,
    "stop_count": 43
  },
  "features": [ … ]
}
```

Coordinates are **`[longitude, latitude]`** per the GeoJSON spec — the reverse of
the order the Python code passes internally.

## Features

Every feature carries a `kind` discriminator. Filter on it.

| `kind` | Geometry | One per |
|---|---|---|
| `actual` | LineString | route |
| `optimal` | LineString | benchmarked route |
| `stop` | Point | stop |
| `depot` | Point | distinct depot coordinate |
| `migration` | LineString | migrated stop |

### `actual`

The plan as uploaded: depot → stops in planned order → depot (closed).

```jsonc
{
  "kind": "actual",
  "route_id": "R001",
  "geometry_quality": "exact",      // per-line; the fleet may be mixed
  "stop_count": 8,
  "finding_ids": ["a1b2c3d4"],      // findings referencing this route
  "total_distance_miles": 12.5,     // REAL road distance
  "total_time_hours": 2.1,
  "sequencing_index": 1.42,         // null when < 2 stops
  "distance_gap_pct": 12.3          // null when not benchmarked; MAY BE NEGATIVE
}
```

### `optimal`

The solver's tour: depot → stops in solver order → depot. **Only present when
the route was benchmarked and the solver returned a reorderable sequence** — a
1-stop route has nothing to reorder. Absence is normal; render the toggle as
unavailable, not broken.

```jsonc
{
  "kind": "optimal",
  "route_id": "R001",
  "geometry_quality": "exact",
  "stop_order": [3, 1, 2],          // matrix indices in solver visit order
  "total_distance_miles": 11.0,
  "total_time_hours": 1.9,
  "distance_gap_pct": 12.3,
  "improvement_gap_pct": 12.3
}
```

### `stop`

```jsonc
{
  "kind": "stop",
  "route_id": "R001",
  "stop_sequence": 3,
  "customer_id": "ACME",          // nullable
  "address": "…",                 // nullable
  "stop_type": "delivery",
  "service_time_minutes": 5.0,
  "time_window_start": "09:00",   // nullable, HH:MM local
  "time_window_end": "17:30",     // nullable
  "planned_arrival_time": "…",    // nullable, ISO 8601
  "demand_units": 12.0,           // nullable
  "has_violation": false,         // route carries a compliance finding
  "finding_ids": []               // findings referencing this specific stop
}
```

### `depot`

**Deduplicated by coordinate.** A six-route fleet sharing one depot emits *one*
marker listing all six route ids — stacking six identical markers renders as one
and breaks click targets.

```jsonc
{ "kind": "depot", "route_ids": ["R001", "R002", "R003"] }
```

### `migration`

A stop the fleet solver would rather serve from a different route. The line runs
**from the stop to the target route's depot** — enough to show the pull, without
implying a drivable path. Only emitted when both endpoints resolve.

```jsonc
{
  "kind": "migration",
  "route_id": "R001",
  "stop_sequence": 4,
  "customer_id": "ACME",
  "from_route": "R001",
  "to_route": "R002"
}
```

## Two things the UI must get right

**Negative gaps are a real outcome, not an error.** `distance_gap_pct` and
`improvement_gap_pct` may be zero or negative, meaning the solver found nothing
better than the plan. Render that as *"plan is within solver reach — no material
savings found"*. Never show a negative number as a saving, and never clamp it to
zero. See Phase 10.5 Part B.

**Findings link both ways.** `finding_ids` on routes and stops is the join key
for findings↔map highlighting. A finding referencing a route puts its id on that
`actual` feature; a finding referencing a specific stop puts it on that `stop`
feature. Both can be empty.

## Absences to design for

- No `optimal` → benchmark off, or nothing to reorder.
- `has_fleet_benchmark: false` → no `migration` features; the fleet solver is
  skipped for single-route fleets, fleets whose routes do not share one depot,
  and fleets above the 300-stop cap. `analysis.json`'s `analyses_skipped`
  carries the reason.
- No `bbox` → empty fleet.
