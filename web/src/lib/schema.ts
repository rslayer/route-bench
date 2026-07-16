/**
 * The RouteBench CSV schema.
 *
 * One source for the downloadable template, the column reference table, and the
 * mapper's auto-suggestions — three things that would drift apart if each held
 * its own copy.
 *
 * Mirrors src/routebench/core/validation.py (REQUIRED_COLUMNS / OPTIONAL_COLUMNS)
 * and data/samples/v1/sample_fleet.csv. The backend validates regardless; this is
 * about giving the user a fighting chance before the upload, not about enforcing.
 */

export interface SchemaField {
  name: string;
  required: boolean;
  description: string;
  example: string;
  /** Lowercased header spellings a real file might use for this field. */
  aliases: string[];
}

/**
 * The depot row is the thing most first-time uploads get wrong: the validator
 * rejects a route with no stop_sequence=0 row (MISSING_DEPOT), because that row
 * carries the route's depot coordinates.
 */
export const DEPOT_ROW_NOTE =
  "Each route needs one row with stop_sequence = 0 — the depot it starts and ends at. Stops are numbered 1, 2, 3… from there.";

export const SCHEMA: readonly SchemaField[] = [
  {
    name: "route_id",
    required: true,
    description: "Identifier for the route. Rows sharing an id form one route.",
    example: "R001",
    aliases: ["route", "routeid", "route id", "route_number", "routenumber", "vehicle", "vehicle_id", "trip_id", "tour_id"],
  },
  {
    name: "stop_sequence",
    required: true,
    description: "Visit order within the route. 0 is the depot; stops start at 1.",
    example: "1",
    aliases: ["sequence", "seq", "stop_no", "stop_number", "stopnum", "order", "stop_order", "position", "idx", "index"],
  },
  {
    name: "latitude",
    required: true,
    description: "Decimal degrees, -90 to 90.",
    example: "32.7767",
    aliases: ["lat", "y", "lat_deg", "latitude_deg", "stop_lat", "stop_latitude"],
  },
  {
    name: "longitude",
    required: true,
    description: "Decimal degrees, -180 to 180.",
    example: "-96.7970",
    aliases: ["lon", "lng", "long", "x", "lon_deg", "longitude_deg", "stop_lon", "stop_lng", "stop_longitude"],
  },
  {
    name: "stop_type",
    required: false,
    description: "depot, delivery, or pickup. Defaults to delivery.",
    example: "delivery",
    aliases: ["type", "kind", "stop_kind", "activity", "activity_type"],
  },
  {
    name: "planned_start_time",
    required: false,
    description: "When the route leaves the depot (ISO 8601). Set on the depot row.",
    example: "2025-03-11T07:30:00+00:00",
    aliases: ["start_time", "starttime", "route_start", "depart_time", "departure_time", "dispatch_time", "shift_start"],
  },
  {
    name: "planned_arrival_time",
    required: false,
    description: "Planned arrival at this stop (ISO 8601).",
    example: "2025-03-11T08:05:00+00:00",
    aliases: ["arrival_time", "arrivaltime", "eta", "planned_eta", "arrive_at", "arrival"],
  },
  {
    name: "service_time_minutes",
    required: false,
    description: "Minutes spent at the stop. Defaults to 5.",
    example: "5",
    aliases: ["service_time", "servicetime", "dwell", "dwell_time", "dwell_minutes", "duration", "service_mins", "stop_duration"],
  },
  {
    name: "time_window_start",
    required: false,
    description: "Earliest the customer accepts service (HH:MM).",
    example: "09:00",
    aliases: ["window_start", "tw_open", "tw_start", "open", "open_time", "earliest", "ready_time", "from_time"],
  },
  {
    name: "time_window_end",
    required: false,
    description: "Latest the customer accepts service (HH:MM).",
    example: "17:00",
    aliases: ["window_end", "tw_close", "tw_end", "close", "close_time", "latest", "due_time", "to_time", "deadline"],
  },
  {
    name: "vehicle_capacity_units",
    required: false,
    description: "Vehicle capacity in units. Set on the depot row.",
    example: "60",
    aliases: ["capacity", "capacity_units", "vehicle_capacity", "max_units", "veh_capacity"],
  },
  {
    name: "demand_units",
    required: false,
    description: "Units to drop at this stop.",
    example: "3",
    aliases: ["demand", "units", "quantity", "qty", "size", "load", "pieces", "parcels"],
  },
  {
    name: "customer_id",
    required: false,
    description: "Customer or order reference, shown on the map.",
    example: "ACME-1",
    aliases: ["customer", "custid", "client_id", "account", "order_id", "shipment_id", "name"],
  },
] as const;

export const REQUIRED_FIELDS = SCHEMA.filter((f) => f.required).map((f) => f.name);
export const FIELD_NAMES = SCHEMA.map((f) => f.name);

/** Header text reduced to a comparable core: "Stop Lat (deg)" -> "stoplatdeg". */
export function normalizeHeader(header: string): string {
  return header.toLowerCase().replace(/[\s_\-.]+/g, "").replace(/[()[\]{}]/g, "").trim();
}

/**
 * Best guess at which schema field a header means, or null.
 *
 * Deterministic and local — no LLM (a non-goal), and no network. Exact and alias
 * matches only; a containment pass would map "latitude_of_depot" to latitude but
 * would also map "delivery_latitude_flag", and a wrong auto-mapping the user
 * accepts without reading is worse than no suggestion at all.
 */
export function suggestField(header: string): string | null {
  const normalized = normalizeHeader(header);
  if (!normalized) return null;

  for (const field of SCHEMA) {
    if (normalizeHeader(field.name) === normalized) return field.name;
  }
  for (const field of SCHEMA) {
    if (field.aliases.some((alias) => normalizeHeader(alias) === normalized)) {
      return field.name;
    }
  }
  return null;
}

/** True when every required field is present, so the mapper can be skipped. */
export function headersMatchSchema(headers: readonly string[]): boolean {
  const present = new Set(headers.map(normalizeHeader));
  return REQUIRED_FIELDS.every((name) => present.has(normalizeHeader(name)));
}

/** The downloadable template: header row plus one depot row and two stops. */
export function templateCsv(): string {
  const header = FIELD_NAMES.join(",");
  const depot =
    "R001,0,32.7767,-96.7970,depot,2025-03-11T07:30:00+00:00,,0,,,60,0,";
  const stop1 =
    "R001,1,32.7850,-96.8050,delivery,,2025-03-11T07:55:00+00:00,5,09:00,17:00,,3,ACME-1";
  const stop2 =
    "R001,2,32.7920,-96.8100,delivery,,2025-03-11T08:15:00+00:00,5,09:00,17:00,,2,ACME-2";
  return [header, depot, stop1, stop2].join("\n") + "\n";
}
