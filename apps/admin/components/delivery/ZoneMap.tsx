'use client';

import { useMemo, useState } from 'react';
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

const PROVIDER_STYLE: Record<string, { fill: string; stroke: string; label: string }> = {
  lalamove: { fill: '#2563eb', stroke: '#1d4ed8', label: 'Courier API' },
  third_party: { fill: '#94a3b8', stroke: '#64748b', label: 'Third party' },
};

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

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="w-full h-auto bg-gray-50 border border-gray-200"
        onMouseLeave={() => { setHovered(null); onSelect?.(null); }}
        onMouseMove={e => {
          const box = e.currentTarget.getBoundingClientRect();
          setCursor({
            x: e.clientX - box.left,
            y: e.clientY - box.top,
            width: box.width,
          });
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
          {active.region_slug && (
            <p className="text-[10px] font-body text-gray-400 mt-0.5">{active.region_slug}</p>
          )}
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
          Hover a zone for its fee. Smaller zones sit on top, which is the order fees resolve in.
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
