"use client";

import { useEffect, useId, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type { DietaryFilter, SpecOverride, TransportMode } from "@/lib/types";

// Transport profiles tracked to the GrabMaps routing API surface
// (docs/grabmaps_api_reference.md §Routing). Phase 0's `TransportMode` union
// still carries the v1 strings; `transport_mode` on the posted override is
// cast to `TransportMode` until Phase 7 aligns the shared type. Integration
// TODO filed on /prism/INTEGRATION_TODOS.md.
const TRANSPORT_PROFILES = [
  "driving",
  "motorcycle",
  "tricycle",
  "cycling",
  "walking",
] as const;
export type TransportProfile = (typeof TRANSPORT_PROFILES)[number];

const AREAS: ReadonlyArray<string> = [
  "Geylang",
  "Chinatown",
  "Little India",
  "Marina Bay",
  "Orchard",
  "Bugis",
  "Tiong Bahru",
  "Botanic Gardens",
  "Holland Village",
  "Sentosa",
];

const VIBE_TAGS: ReadonlyArray<string> = [
  "chill",
  "adventurous",
  "photogenic",
  "foodie",
  "cultural",
  "romantic",
  "family",
  "nature",
  "nightlife",
];

const DIETARY: ReadonlyArray<{ value: string; label: string }> = [
  { value: "none", label: "none" },
  { value: "halal", label: "halal" },
  { value: "vegetarian", label: "vegetarian" },
  { value: "vegan", label: "vegan" },
];

export interface RaceFormState {
  area: string;
  startTime: string;
  durationHours: number;
  partySize: number;
  mode: TransportProfile;
  budgetSgd: number;
  vibe: string[];
  dietary: string;
  accessible: boolean;
  notes: string;
}

function defaultStartTime(): string {
  const now = new Date();
  now.setMinutes(0, 0, 0);
  now.setHours(now.getHours() + 1);
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(
    now.getDate(),
  )}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
}

// `startTime` is populated in a client-only effect; calling
// defaultStartTime() at module scope would execute on both the SSR pass and
// the hydration pass, producing two different strings and tripping React's
// hydration mismatch warning.
const DEFAULT_FORM: RaceFormState = {
  area: "Geylang",
  startTime: "",
  durationHours: 4,
  partySize: 2,
  mode: "walking",
  budgetSgd: 40,
  vibe: ["foodie"],
  dietary: "none",
  accessible: false,
  notes: "",
};

function composeRequest(f: RaceFormState): {
  query: string;
  spec_override: SpecOverride;
} {
  const parts: string[] = [];
  parts.push(`${f.durationHours}h in ${f.area || "Singapore"}`);
  parts.push(`budget SGD ${f.budgetSgd}`);
  parts.push(`${f.partySize} ${f.partySize === 1 ? "person" : "people"}`);
  parts.push(f.mode);
  if (f.vibe.length) parts.push(`${f.vibe.join(" + ")} vibe`);
  if (f.dietary !== "none") parts.push(f.dietary);
  if (f.accessible) parts.push("wheelchair-friendly");
  if (f.startTime) parts.push(`starting ${f.startTime}`);
  if (f.notes.trim()) parts.push(`Notes: ${f.notes.trim()}`);
  const query = parts.join(", ");

  const spec_override: SpecOverride = {
    area: f.area || null,
    max_duration_minutes: Math.round(f.durationHours * 60),
    max_budget_sgd: f.budgetSgd,
    transport_mode: f.mode as unknown as TransportMode,
    dietary: f.dietary === "none" ? null : (f.dietary as DietaryFilter),
    mood_tags: f.vibe,
    start_time_iso: f.startTime || null,
    party_size: f.partySize,
    accessible: f.accessible,
  };
  return { query, spec_override };
}

export interface StructuredRaceFormProps {
  onLaunch: (query: string, spec_override: SpecOverride) => void;
  disabled: boolean;
  preset?: Partial<RaceFormState> | null;
}

/**
 * Structured input surface for launching a race. Composes a natural-language
 * query plus a strict `SpecOverride` that replaces the brittle LLM spec
 * parser for fields the form samples directly. The live preview under the
 * form makes the field→query mapping obvious during the demo.
 */
export function StructuredRaceForm({
  onLaunch,
  disabled,
  preset = null,
}: StructuredRaceFormProps) {
  const [form, setForm] = useState<RaceFormState>(DEFAULT_FORM);
  const [areaTouched, setAreaTouched] = useState(false);

  useEffect(() => {
    setForm((prev) =>
      prev.startTime === "" ? { ...prev, startTime: defaultStartTime() } : prev,
    );
  }, []);

  useEffect(() => {
    if (!preset) return;
    const clean: Partial<RaceFormState> = {};
    for (const [k, v] of Object.entries(preset)) {
      if (v === undefined) continue;
      if (typeof v === "number" && Number.isNaN(v)) continue;
      (clean as Record<string, unknown>)[k] = v;
    }
    if (Object.keys(clean).length === 0) return;
    setForm((prev) => ({ ...prev, ...clean }));
  }, [preset]);

  const areaId = useId();
  const startId = useId();
  const durId = useId();
  const partyId = useId();
  const budgetId = useId();
  const notesId = useId();
  const modeId = useId();

  const toggleVibe = (tag: string): void =>
    setForm((f) => ({
      ...f,
      vibe: f.vibe.includes(tag)
        ? f.vibe.filter((t) => t !== tag)
        : [...f.vibe, tag],
    }));

  const previewQuery = useMemo(() => composeRequest(form).query, [form]);
  const areaMissing = form.area.trim() === "";

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (disabled) return;
        if (areaMissing) {
          setAreaTouched(true);
          return;
        }
        if (form.startTime === "") return;
        const { query, spec_override } = composeRequest(form);
        onLaunch(query, spec_override);
      }}
    >
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label htmlFor={areaId} className="mb-1 block text-xs text-white/50">
            start location
          </Label>
          <Input
            id={areaId}
            list="areas-datalist"
            required
            aria-invalid={areaTouched && areaMissing}
            aria-describedby={
              areaTouched && areaMissing ? `${areaId}-error` : undefined
            }
            value={form.area}
            onChange={(e) => setForm((f) => ({ ...f, area: e.target.value }))}
            onBlur={() => setAreaTouched(true)}
            className="text-sm"
          />
          {areaTouched && areaMissing && (
            <div
              id={`${areaId}-error`}
              role="alert"
              className="mt-1 text-[10px] text-red-400/80"
            >
              start location is required
            </div>
          )}
          <datalist id="areas-datalist">
            {AREAS.map((a) => (
              <option key={a} value={a} />
            ))}
          </datalist>
        </div>

        <div>
          <Label htmlFor={startId} className="mb-1 block text-xs text-white/50">
            start time
          </Label>
          <Input
            id={startId}
            type="datetime-local"
            value={form.startTime}
            onChange={(e) =>
              setForm((f) => ({ ...f, startTime: e.target.value }))
            }
            className="text-sm"
          />
        </div>

        <div>
          <Label htmlFor={durId} className="mb-1 block text-xs text-white/50">
            duration (hours)
          </Label>
          <Input
            id={durId}
            type="number"
            min={1}
            max={12}
            step={0.5}
            value={form.durationHours}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                durationHours: Number(e.target.value) || 1,
              }))
            }
            className="text-sm font-mono tabular-nums"
          />
        </div>

        <div>
          <Label htmlFor={partyId} className="mb-1 block text-xs text-white/50">
            party size
          </Label>
          <Input
            id={partyId}
            type="number"
            min={1}
            max={12}
            step={1}
            value={form.partySize}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                partySize: Number(e.target.value) || 1,
              }))
            }
            className="text-sm font-mono tabular-nums"
          />
        </div>
      </div>

      <div>
        <Label htmlFor={modeId} className="mb-1 block text-xs text-white/50">
          transport
        </Label>
        <Select
          value={form.mode}
          onValueChange={(v) =>
            setForm((f) => ({ ...f, mode: v as TransportProfile }))
          }
        >
          <SelectTrigger id={modeId} className="w-full text-sm">
            <SelectValue placeholder="choose a profile" />
          </SelectTrigger>
          <SelectContent>
            {TRANSPORT_PROFILES.map((p) => (
              <SelectItem key={p} value={p}>
                {p}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div>
        <Label htmlFor={budgetId} className="mb-1 block text-xs text-white/50">
          budget ceiling (SGD)
        </Label>
        <Input
          id={budgetId}
          type="number"
          min={5}
          max={500}
          step={5}
          value={form.budgetSgd}
          onChange={(e) =>
            setForm((f) => ({ ...f, budgetSgd: Number(e.target.value) || 0 }))
          }
          className="text-sm font-mono tabular-nums"
        />
      </div>

      <fieldset>
        <legend className="mb-1 text-xs text-white/50">vibe</legend>
        <div className="flex flex-wrap gap-1">
          {VIBE_TAGS.map((tag) => {
            const on = form.vibe.includes(tag);
            return (
              <Badge
                key={tag}
                render={
                  <button
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggleVibe(tag)}
                  />
                }
                variant={on ? "default" : "outline"}
                className={
                  on
                    ? "cursor-pointer bg-grab-green/20 text-grab-green border-grab-green/40 hover:bg-grab-green/30"
                    : "cursor-pointer hover:bg-white/10"
                }
              >
                {tag}
              </Badge>
            );
          })}
        </div>
      </fieldset>

      <fieldset>
        <legend className="mb-1 text-xs text-white/50">dietary</legend>
        <div role="radiogroup" aria-label="dietary" className="flex gap-1">
          {DIETARY.map((d) => {
            const active = form.dietary === d.value;
            return (
              <Button
                key={d.value}
                type="button"
                role="radio"
                aria-checked={active}
                size="sm"
                variant={active ? "default" : "outline"}
                onClick={() => setForm((f) => ({ ...f, dietary: d.value }))}
                className={
                  active
                    ? "flex-1 bg-grab-green/20 border-grab-green/40 text-grab-green hover:bg-grab-green/30"
                    : "flex-1"
                }
              >
                {d.label}
              </Button>
            );
          })}
        </div>
      </fieldset>

      <label className="flex cursor-pointer items-center gap-2 text-xs text-white/70">
        <input
          type="checkbox"
          checked={form.accessible}
          onChange={(e) =>
            setForm((f) => ({ ...f, accessible: e.target.checked }))
          }
          className="accent-grab-green focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60"
        />
        wheelchair-friendly only
      </label>

      <div>
        <Label htmlFor={notesId} className="mb-1 block text-xs text-white/50">
          notes
        </Label>
        <Textarea
          id={notesId}
          rows={2}
          value={form.notes}
          onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
          maxLength={500}
          placeholder="anything the structured fields missed"
          className="resize-none text-xs"
        />
      </div>

      <div className="rounded border border-white/10 bg-white/[0.02] p-2 font-mono text-[11px] leading-relaxed text-white/40 break-words">
        query → {previewQuery}
      </div>

      <Button
        type="submit"
        disabled={disabled}
        className="w-full bg-grab-green/20 border-grab-green/40 text-grab-green hover:bg-grab-green/30"
      >
        {disabled ? "racing…" : "launch race"}
      </Button>
    </form>
  );
}
