"use client";

import Image from "next/image";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import type { StreetviewPhoto } from "@/lib/types";

export interface StreetViewGalleryProps {
  photos: StreetviewPhoto[] | null | undefined;
  /** Accessible label for the gallery region (e.g. the POI name). */
  label: string;
  /** Optional empty-state message; defaults to "no street-view photos". */
  emptyLabel?: string;
}

/**
 * Horizontal carousel of OpenStreetCam thumbnails for a POI. SPHERE photos
 * carry a small 360° badge to distinguish panoramic captures from flat
 * PLANE shots (grabmaps_api_reference.md §OpenStreetCam). Clicking a
 * thumbnail opens a lightbox dialog with the full-resolution image. The
 * component is deliberately self-contained — no external carousel dep —
 * so the rest of the plan-detail surface stays dependency-light.
 */
export function StreetViewGallery({
  photos,
  label,
  emptyLabel = "no street-view photos",
}: StreetViewGalleryProps) {
  const [active, setActive] = useState<StreetviewPhoto | null>(null);
  const safe = photos ?? [];

  if (safe.length === 0) {
    return (
      <div
        role="status"
        aria-label={`${label}: ${emptyLabel}`}
        className="rounded border border-dashed border-white/10 bg-white/[0.02] px-3 py-4 text-center text-[10px] text-white/30"
      >
        {emptyLabel}
      </div>
    );
  }

  return (
    <>
      <ScrollArea
        aria-label={`Street-view photos for ${label}`}
        className="w-full whitespace-nowrap"
      >
        <div className="flex gap-2 pb-2">
          {safe.map((photo, i) => {
            const isPano = photo.projection === "SPHERE";
            const thumb = photo.thumb_url ?? photo.url;
            return (
              <button
                key={`${photo.url}-${i}`}
                type="button"
                onClick={() => setActive(photo)}
                className="group/thumb relative h-24 w-36 shrink-0 overflow-hidden rounded border border-white/10 bg-black transition-colors hover:border-grab-green/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60"
                aria-label={
                  isPano
                    ? `Open 360° panorama ${i + 1} of ${safe.length}`
                    : `Open photo ${i + 1} of ${safe.length}`
                }
              >
                <Image
                  src={thumb}
                  alt=""
                  fill
                  sizes="144px"
                  unoptimized
                  className="object-cover transition-transform group-hover/thumb:scale-105"
                />
                {isPano && (
                  <Badge
                    variant="secondary"
                    className="absolute bottom-1 right-1 h-4 bg-black/70 text-[9px] font-mono tracking-tight text-white/90"
                  >
                    360°
                  </Badge>
                )}
              </button>
            );
          })}
        </div>
        <ScrollBar orientation="horizontal" />
      </ScrollArea>

      <Dialog
        open={active !== null}
        onOpenChange={(open) => {
          if (!open) setActive(null);
        }}
      >
        <DialogContent
          className="max-w-[min(90vw,1024px)] gap-2 bg-black/95 p-3 ring-white/10"
          showCloseButton
        >
          <DialogTitle className="text-xs font-mono text-white/50">
            {label}
            {active?.projection === "SPHERE" ? " · 360°" : ""}
            {typeof active?.heading === "number"
              ? ` · heading ${Math.round(active.heading)}°`
              : ""}
          </DialogTitle>
          {active && (
            <div className="relative w-full" style={{ aspectRatio: "16 / 9" }}>
              <Image
                src={active.url}
                alt={`Street-view photo for ${label}`}
                fill
                sizes="(max-width: 1024px) 90vw, 1024px"
                unoptimized
                className="rounded object-contain"
              />
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
