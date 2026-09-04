'use client';

import { useMemo, useRef, useState } from 'react';
import type { DeliveryZoneMap, DeliveryZoneShape } from '@/lib/types';
import { formatCurrency } from '@/lib/utils';

/**
 * The delivery map, drawn.
 *
 * Plain SVG rather than a tiled basemap. These shapes tile the entire country
 * with no gaps, so a satellite image underneath would be almost completely
 * covered by them — it would cost an API key, a script from a third party and
 * a per-load fee to render something nobody can see. What the screen has to
 * answer is "which areas are which price", and an outline answers that.
 *
 * Equirectangular, with longitude squashed by cos(latitude). At the UAE's
 * latitude a degree of longitude is about 100 km against 111 km for latitude,
 * and without the correction the country comes out visibly stretched.
 */

/**
 * One entry per `FulfilmentProviderEnum` value.
 *
 * `noon_send` was missing, and the `?? PROVIDER_STYLE.third_party` fallback at
 * every read site meant the omission was invisible: Sharjah Central — the one
 * zone we deliver ourselves in under an hour, and the cheapest run on the map —
 * rendered grey and was labelled "Third party" in the tooltip and the legend.
 * A map whose whole job is "which areas are which courier" was answering wrong
 * for the courier that matters most.
 *
 * Green matches the "NOON SEND" pill in the fees table, so the two screens
 * describe the same zone the same way.
 */
const PROVIDER_STYLE: Record<string, { fill: string; stroke: string; label: string }> = {
  noon_send: { fill: '#16a34a', stroke: '#15803d', label: 'noon Send' },
  lalamove: { fill: '#2563eb', stroke: '#1d4ed8', label: 'Lalamove' },
  // The two Slider fleets share amber with the fees-table badge, but take
  // neighbouring shades so a bike zone and a car zone are told apart on the map:
  // the bike lighter, the car a deeper burnt orange. Legacy bare `slider` keeps
  // the original amber it always drew in.
  slider_bike: { fill: '#f59e0b', stroke: '#d97706', label: 'Slider (bike)' },
  slider_car: { fill: '#b45309', stroke: '#92400e', label: 'Slider (car)' },
  slider: { fill: '#f59e0b', stroke: '#d97706', label: 'Slider' },
  third_party: { fill: '#94a3b8', stroke: '#64748b', label: 'Third party' },
};

/** How far in a wheel notch takes you, and the limits. */
const ZOOM_STEP = 1.18;
const MIN_SCALE = 1;
const MAX_SCALE = 40;

const WIDTH = 900;
const HEIGHT = 640;
const PADDING = 16;

interface Props {
  data: DeliveryZoneMap;
  /** Highlighted from outside — hovering a row in the table lights up its shape. */
  selectedZoneId?: string | null;
  onSelect?: (zoneId: string | null) => void;
}

export function ZoneMap({ data, selectedZoneId, onSelect }: Props) {
  const [hovered, setHovered] = useState<DeliveryZoneShape | null>(null);
  /**
   * The window onto the map, in viewBox units.
   *
   * Zoom is a `viewBox` change rather than a CSS transform so the strokes stay
   * the width they were drawn at — a scaled-up transform would thicken every
   * boundary until the small city zones disappeared under their own outlines,
   * which is the opposite of what zooming in is for.
   */
  const [view, setView] = useState({ x: 0, y: 0, w: WIDTH, h: HEIGHT });
  const [panning, setPanning] = useState<{ x: number; y: number } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  // Rendered width travels with the cursor because the SVG scales to its
  // container: the viewBox is 900 units wide and the panel may be 560 pixels,
  // so clamping the tooltip against the viewBox would never actually clamp.
  const [cursor, setCursor] = useState({ x: 0, y: 0, width: WIDTH });

  const projected = useMemo(() => project(data), [data]);

  if (!projected) {
    return (
      <div className="flex items-center justify-center h-64 text-xs font-body text-gray-400">
        No zones to draw.
      </div>
    );
  }

  const active = hovered ?? projected.zones.find(z => z.zone.id === selectedZoneId)?.zone ?? null;

  /** Screen pixels -> viewBox units, which is what panning has to move by. */
  function toViewBox(e: { clientX: number; clientY: number }) {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box) return { x: 0, y: 0 };
    return {
      x: view.x + ((e.clientX - box.left) / box.width) * view.w,
      y: view.y + ((e.clientY - box.top) / box.height) * view.h,
    };
  }

  /** Zoom about the cursor, so the shape under the pointer stays under it. */
  function zoomAt(anchor: { x: number; y: number }, factor: number) {
    setView(v => {
      const scale = WIDTH / v.w;
      const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor));
      const w = WIDTH / next;
      const h = HEIGHT / next;
      // Keep the anchor at the same fraction across the box.
      const fx = (anchor.x - v.x) / v.w;
      const fy = (anchor.y - v.y) / v.h;
      return {
        w,
        h,
        // Clamped so the country cannot be dragged off the panel entirely.
        x: Math.min(Math.max(anchor.x - fx * w, 0), WIDTH - w),
        y: Math.min(Math.max(anchor.y - fy * h, 0), HEIGHT - h),
      };
    });
  }

  const zoomedIn = view.w < WIDTH;

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
        className={`w-full h-auto bg-gray-50 border border-gray-200 ${
          panning ? 'cursor-grabbing' : zoomedIn ? 'cursor-grab' : 'cursor-default'
        }`}
        onWheel={e => {
          // No `preventDefault` — React attaches wheel passively, so the page
          // would scroll too. Zoom only with a modifier held, which is also the
          // convention that stops a map hijacking an ordinary page scroll.
          if (!e.ctrlKey && !e.metaKey && !e.shiftKey) return;
          zoomAt(toViewBox(e), e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP);
        }}
        onMouseDown={e => setPanning(toViewBox(e))}
        onMouseUp={() => setPanning(null)}
        onDoubleClick={() => setView({ x: 0, y: 0, w: WIDTH, h: HEIGHT })}
        onMouseLeave={() => { setHovered(null); onSelect?.(null); setPanning(null); }}
        onMouseMove={e => {
          const box = e.currentTarget.getBoundingClientRect();
          setCursor({
            x: e.clientX - box.left,
            y: e.clientY - box.top,
            width: box.width,
          });
          if (!panning) return;
          const at = toViewBox(e);
          setView(v => ({
            ...v,
            x: Math.min(Math.max(v.x - (at.x - panning.x), 0), WIDTH - v.w),
            y: Math.min(Math.max(v.y - (at.y - panning.y), 0), HEIGHT - v.h),
          }));
        }}
      >
        {/* The zones tile the country without overlapping — a served city is
            punched out of its emirate rather than laid over it — so nothing
            here depends on paint order. Largest first anyway, so a stray
            hairline of a big shape never sits over a small one. */}
        {projected.zones.map(({ zone, path }) => {
          const style = PROVIDER_STYLE[zone.fulfilment_provider] ?? PROVIDER_STYLE.third_party;
          const isActive = active?.id === zone.id;
          return (
            <path
              key={zone.id}
              d={path}
              fill={style.fill}
              // Rings after the first are holes. Even-odd is what makes them
              // read as holes rather than as another filled island.
              fillRule="evenodd"
              fillOpacity={isActive ? 0.55 : 0.22}
              stroke={style.stroke}
              strokeWidth={isActive ? 1.6 : 0.7}
              strokeLinejoin="round"
              className="cursor-pointer transition-[fill-opacity]"
              onMouseEnter={() => { setHovered(zone); onSelect?.(zone.id); }}
            />
          );
        })}
      </svg>

      {/* Buttons as well as the wheel: the city zones are a few pixels across
          at full extent, and a trackpad pinch is not something every admin
          reaches for. */}
      <div className="absolute top-2 right-2 flex flex-col gap-1">
        {[
          ['+', () => zoomAt({ x: view.x + view.w / 2, y: view.y + view.h / 2 }, ZOOM_STEP)],
          ['−', () => zoomAt({ x: view.x + view.w / 2, y: view.y + view.h / 2 }, 1 / ZOOM_STEP)],
        ].map(([label, onClick]) => (
          <button
            key={label as string}
            type="button"
            onClick={onClick as () => void}
            className="w-6 h-6 bg-white/90 border border-gray-300 text-gray-600 text-sm leading-none hover:bg-white"
          >
            {label as string}
          </button>
        ))}
        {zoomedIn && (
          <button
            type="button"
            onClick={() => setView({ x: 0, y: 0, w: WIDTH, h: HEIGHT })}
            className="w-6 h-6 bg-white/90 border border-gray-300 text-gray-500 text-[10px] leading-none hover:bg-white"
            title="Fit the whole country"
          >
            ⤢
          </button>
        )}
      </div>

      {active && (
        <div
          className="pointer-events-none absolute z-10 bg-white border border-gray-300 shadow-sm px-3 py-2"
          style={{
            // Nudged off the pointer and clamped so a zone near the right edge
            // does not push the card off the panel.
            left: Math.max(0, Math.min(cursor.x + 14, cursor.width - 200)),
            top: Math.max(cursor.y - 10, 0),
          }}
        >
          <p className="text-xs font-body text-gray-800">{active.name}</p>
          <p className="text-[11px] font-body text-gray-500 mt-0.5">
            {formatCurrency(active.delivery_fee)} ·{' '}
            {(PROVIDER_STYLE[active.fulfilment_provider] ?? PROVIDER_STYLE.third_party).label}
          </p>
        </div>
      )}

      <div className="flex items-center gap-4 mt-2">
        {Object.entries(PROVIDER_STYLE).map(([key, style]) => (
          <div key={key} className="flex items-center gap-1.5">
            <span
              className="inline-block w-3 h-3 border"
              style={{ backgroundColor: style.fill, opacity: 0.5, borderColor: style.stroke }}
            />
            <span className="text-[11px] font-body text-gray-500">{style.label}</span>
          </div>
        ))}
        <span className="text-[11px] font-body text-gray-400 ml-auto">
          Hover a zone for its fee. ⌘/Ctrl + scroll to zoom, drag to pan,
          double-click to fit.
        </span>
      </div>
    </div>
  );
}

// ── Projection ────────────────────────────────────────────────────────────────

interface Projected {
  zones: Array<{ zone: DeliveryZoneShape; path: string }>;
}

function project(data: DeliveryZoneMap): Projected | null {
  if (!data.bounds || !data.zones.length) return null;
  const { min_lat, max_lat, min_lng, max_lng } = data.bounds;

  // A degree of longitude is shorter than a degree of latitude everywhere but
  // the equator. Without this the country is stretched east-west by a tenth.
  const lngScale = Math.cos((((min_lat + max_lat) / 2) * Math.PI) / 180);
  const spanX = (max_lng - min_lng) * lngScale;
  const spanY = max_lat - min_lat;
  if (spanX <= 0 || spanY <= 0) return null;

  const scale = Math.min((WIDTH - PADDING * 2) / spanX, (HEIGHT - PADDING * 2) / spanY);
  const offsetX = (WIDTH - spanX * scale) / 2;
  const offsetY = (HEIGHT - spanY * scale) / 2;

  const toX = (lng: number) => offsetX + (lng - min_lng) * lngScale * scale;
  // Latitude grows north, screen y grows down.
  const toY = (lat: number) => offsetY + (max_lat - lat) * scale;

  const zones = data.zones
    .map(zone => ({ zone, path: toPath(zone, toX, toY) }))
    .filter(entry => entry.path.length > 0)
    // Biggest first so the small, specific zones are painted last and are the
    // ones the cursor actually lands on.
    .sort((a, b) => b.path.length - a.path.length);

  return { zones };
}

function toPath(
  zone: DeliveryZoneShape,
  toX: (lng: number) => number,
  toY: (lat: number) => number,
): string {
  const parts: string[] = [];
  for (const polygon of zone.geometry?.coordinates ?? []) {
    for (const ring of polygon) {
      if (ring.length < 3) continue;
      const points = ring.map(([lng, lat]) => `${toX(lng).toFixed(1)},${toY(lat).toFixed(1)}`);
      parts.push(`M${points.join('L')}Z`);
    }
  }
  return parts.join(' ');
}
