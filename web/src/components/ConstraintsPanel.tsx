"use client";

import Link from "next/link";
import { UNSUPPORTED_CONSTRAINTS } from "@/lib/constraints";
import { describeDeviations, type PanelState } from "@/lib/config-builder";

/**
 * Analysis settings.
 *
 * Collapsed by default: one decision per screen means the path to a score is
 * drop → confirm → go, and a first-time visitor should not have to form an
 * opinion about lunch breaks to get a grade. Everything here has a working
 * default; opening it is optional.
 *
 * Every control binds to a real config field that the analysis genuinely reads.
 * Two of these (time windows, capacity) had nothing behind them until the
 * solvers were fixed to honour them — a panel whose switches do nothing is
 * worse than no panel.
 */

function Toggle({
  id,
  label,
  description,
  checked,
  onChange,
  children,
}: {
  id: string;
  label: string;
  description: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  children?: React.ReactNode;
}) {
  return (
    <div className="constraint">
      <div className="constraint-head">
        <input
          type="checkbox"
          id={id}
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
        />
        <label htmlFor={id}>{label}</label>
      </div>
      <p className="constraint-desc">{description}</p>
      {checked && children ? <div className="constraint-fields">{children}</div> : null}
    </div>
  );
}

function NumberField({
  id,
  label,
  value,
  onChange,
  unit,
  min,
  max,
  step,
}: {
  id: string;
  label: string;
  value: number;
  onChange: (v: number) => void;
  unit: string;
  min: number;
  max: number;
  step: number;
}) {
  return (
    <span className="numfield">
      <label htmlFor={id}>{label}</label>
      <input
        type="number"
        id={id}
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => {
          const next = Number(e.target.value);
          // An empty input parses to NaN, which would silently poison the
          // config; keep the last good value instead.
          if (!Number.isNaN(next)) onChange(next);
        }}
      />
      <span className="numfield-unit">{unit}</span>
    </span>
  );
}

export default function ConstraintsPanel({
  panel,
  onChange,
  open,
  onOpenChange,
}: {
  panel: PanelState;
  onChange: (panel: PanelState) => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const set = <K extends keyof PanelState>(key: K, value: PanelState[K]) =>
    onChange({ ...panel, [key]: value });

  const deviations = describeDeviations(panel);

  return (
    <details
      className="settings"
      open={open}
      onToggle={(e) => onOpenChange((e.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>
        Analysis settings
        <span className="settings-hint">
          {deviations.length === 0
            ? "Standard assumptions"
            : `${deviations.length} change${deviations.length === 1 ? "" : "s"} from standard`}
        </span>
      </summary>

      <div className="settings-body">
        <p className="settings-lede">
          These are the rules we grade against and re-solve under. Whatever you
          choose here is exactly what runs, and the report records it.
        </p>

        <Toggle
          id="c-windows"
          label="Delivery time windows"
          description="Grade whether stops are reached inside their committed window, and make the solver respect windows when it re-sequences."
          checked={panel.enforceTimeWindows}
          onChange={(v) => set("enforceTimeWindows", v)}
        />

        <div className="constraint">
          <div className="constraint-head">
            <span className="constraint-static">Maximum shift length</span>
          </div>
          <p className="constraint-desc">The longest a driver&rsquo;s day may run, door to door.</p>
          <div className="constraint-fields">
            <NumberField
              id="c-shift"
              label="Cap"
              value={panel.maxShiftHours}
              onChange={(v) => set("maxShiftHours", v)}
              unit="hours"
              min={1}
              max={24}
              step={0.5}
            />
          </div>
        </div>

        <Toggle
          id="c-prepost"
          label="Pre-trip / post-trip time"
          description="Fixed time at the depot before departure and after return."
          checked={panel.prePostTripEnabled}
          onChange={(v) => set("prePostTripEnabled", v)}
        >
          <NumberField
            id="c-pre"
            label="Pre-trip"
            value={panel.preTripMinutes}
            onChange={(v) => set("preTripMinutes", v)}
            unit="min"
            min={0}
            max={120}
            step={5}
          />
          <NumberField
            id="c-post"
            label="Post-trip"
            value={panel.postTripMinutes}
            onChange={(v) => set("postTripMinutes", v)}
            unit="min"
            min={0}
            max={120}
            step={5}
          />
        </Toggle>

        <Toggle
          id="c-lunch"
          label="Lunch break"
          description="A single unpaid break, inserted once the shift passes a threshold."
          checked={panel.lunchEnabled}
          onChange={(v) => set("lunchEnabled", v)}
        >
          <NumberField
            id="c-lunch-min"
            label="Length"
            value={panel.lunchMinutes}
            onChange={(v) => set("lunchMinutes", v)}
            unit="min"
            min={0}
            max={120}
            step={5}
          />
          <NumberField
            id="c-lunch-after"
            label="After"
            value={panel.lunchAfterHours}
            onChange={(v) => set("lunchAfterHours", v)}
            unit="hours"
            min={0}
            max={12}
            step={0.5}
          />
        </Toggle>

        <Toggle
          id="c-capacity"
          label="Vehicle capacity"
          description="Units, weight, or volume limits per vehicle. Applied only where your file carries the columns."
          checked={panel.enforceCapacity}
          onChange={(v) => set("enforceCapacity", v)}
        />

        <div className="constraint">
          <div className="constraint-head">
            <span className="constraint-static">Per-stop service time</span>
          </div>
          <p className="constraint-desc">
            How long a driver spends at each stop. A <code>service_time_minutes</code> column in
            your file overrides this per stop.
          </p>
          <div className="constraint-fields">
            <NumberField
              id="c-service"
              label="Default"
              value={panel.serviceDefaultMinutes}
              onChange={(v) => set("serviceDefaultMinutes", v)}
              unit="min"
              min={0}
              max={240}
              step={1}
            />
          </div>
        </div>

        <div className="constraint">
          <div className="constraint-head">
            <label htmlFor="c-traffic" className="constraint-static">
              Traffic profile
            </label>
          </div>
          <p className="constraint-desc">
            Scale travel times by time of day. Not live traffic — a recurring-congestion
            approximation.
          </p>
          <div className="constraint-fields">
            <select
              id="c-traffic"
              value={panel.traffic}
              onChange={(e) => set("traffic", e.target.value as PanelState["traffic"])}
            >
              <option value="free_flow">Free-flow (no adjustment)</option>
              <option value="urban_us">Urban US (0.75× 07:00–09:00, 0.80× 16:00–18:30)</option>
            </select>
          </div>
        </div>

        <Toggle
          id="c-benchmark"
          label="Fleet benchmark"
          description="Re-optimise stop-to-route assignment across the whole fleet, not just the order within each route."
          checked={panel.includeBenchmark}
          onChange={(v) => set("includeBenchmark", v)}
        />

        <Toggle
          id="c-pdf"
          label="Also produce a PDF"
          description="A printable copy alongside the interactive report. Adds a little time."
          checked={panel.includePdf}
          onChange={(v) => set("includePdf", v)}
        />

        {deviations.length > 0 ? (
          <div className="deviations" role="status">
            <strong>What this changes:</strong>
            <ul>
              {deviations.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </div>
        ) : null}

        <details className="unsupported">
          <summary>Not yet supported ({UNSUPPORTED_CONSTRAINTS.length})</summary>
          <ul>
            {UNSUPPORTED_CONSTRAINTS.map((c) => (
              <li key={c.label}>
                <strong>{c.label}</strong>{" "}
                <span className={`tag tag-${c.tag === "Planned" ? "planned" : "considering"}`}>
                  {c.tag}
                </span>
                <br />
                <span className="dim-note">{c.note}</span>
              </li>
            ))}
          </ul>
          <p className="dim-note">
            <Link href="/how-it-works#not-supported">More on what we model →</Link>
          </p>
        </details>
      </div>
    </details>
  );
}
