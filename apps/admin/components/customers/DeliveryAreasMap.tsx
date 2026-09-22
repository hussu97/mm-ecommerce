'use client';

import { useMemo, useRef, useState } from 'react';
import type { Schemas } from '@mm/types';

type DeliveryAreas = Schemas['CustomerDeliveryAreas'];
type Metric = 'customer_count' | 'revenue' | 'aov';

const WIDTH = 1000;
const HEIGHT = 660;
const PADDING = 26;
const ZOOM_STEP = 1.18;
const MIN_SCALE = 1;
const MAX_SCALE = 40;

function geometryPoints(geometry: Record<string, unknown>): [number, number][] {
  const coordinates = geometry.coordinates;
  if (!Array.isArray(coordinates)) return [];
  const polygons = geometry.type === 'Polygon' ? [coordinates] : coordinates;
  return polygons.flatMap(polygon => {
    if (!Array.isArray(polygon)) return [];
    return polygon.flatMap(ring => {
      if (!Array.isArray(ring)) return [];
      return ring.filter(
        (point): point is [number, number] => Array.isArray(point) && point.length >= 2
          && typeof point[0] === 'number' && typeof point[1] === 'number',
      );
    });
  });
}

function zonePath(
  geometry: Record<string, unknown>,
  project: (longitude: number, latitude: number) => [number, number],
) {
  const coordinates = geometry.coordinates;
  if (!Array.isArray(coordinates)) return '';
  const polygons = geometry.type === 'Polygon' ? [coordinates] : coordinates;
  return polygons.map(polygon => (polygon as unknown[]).map(ring => {
    const commands = (ring as unknown[]).map((point, index) => {
      if (!Array.isArray(point) || typeof point[0] !== 'number' || typeof point[1] !== 'number') return '';
      const [x, y] = project(point[0], point[1]);
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    });
    return `${commands.join(' ')} Z`;
  }).join(' ')).join(' ');
}

function heatColor(ratio: number) {
  // Cold indigo → warm amber → hot rose: low-density cells remain visible
  // against the quiet zone outlines without competing with them.
  const hue = 218 - Math.round(Math.max(0, Math.min(1, ratio)) * 190);
  return `hsl(${hue} 82% 50%)`;
}

export function DeliveryAreasMap({
  data,
  metric,
  showChannelBreakdown,
}: {
  data: DeliveryAreas;
  metric: Metric;
  showChannelBreakdown: boolean;
}) {
  const [hovered, setHovered] = useState<DeliveryAreas['zones'][number] | null>(null);
  const [view, setView] = useState({ x: 0, y: 0, w: WIDTH, h: HEIGHT });
  const [panning, setPanning] = useState<{ x: number; y: number } | null>(null);
  const [cursor, setCursor] = useState({ x: 0, y: 0, width: WIDTH });
  const svgRef = useRef<SVGSVGElement | null>(null);
  const projection = useMemo(() => {
    const points = data.zones.flatMap(zone => geometryPoints(zone.geometry));
    if (!points.length) return null;
    const [minLng, maxLng] = [Math.min(...points.map(point => point[0])), Math.max(...points.map(point => point[0]))];
    const [minLat, maxLat] = [Math.min(...points.map(point => point[1])), Math.max(...points.map(point => point[1]))];
    const midLatRadians = ((minLat + maxLat) / 2) * Math.PI / 180;
    const adjustedWidth = Math.max((maxLng - minLng) * Math.cos(midLatRadians), 0.01);
    const adjustedHeight = Math.max(maxLat - minLat, 0.01);
    const scale = Math.min((WIDTH - PADDING * 2) / adjustedWidth, (HEIGHT - PADDING * 2) / adjustedHeight);
    const contentWidth = adjustedWidth * scale;
    const contentHeight = adjustedHeight * scale;
    const offsetX = (WIDTH - contentWidth) / 2;
    const offsetY = (HEIGHT - contentHeight) / 2;
    return (longitude: number, latitude: number): [number, number] => [
      offsetX + (longitude - minLng) * Math.cos(midLatRadians) * scale,
      HEIGHT - offsetY - (latitude - minLat) * scale,
    ];
  }, [data.zones]);

  const maxValue = Math.max(...data.zones.map(zone => zone[metric]), 1);
  const zoomedIn = view.w < WIDTH;
  if (!projection) {
    return <div className="flex h-80 items-center justify-center text-sm font-body text-gray-400">No geocoded delivery addresses match these filters.</div>;
  }

  function toViewBox(e: { clientX: number; clientY: number }) {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box) return { x: 0, y: 0 };
    return {
      x: view.x + ((e.clientX - box.left) / box.width) * view.w,
      y: view.y + ((e.clientY - box.top) / box.height) * view.h,
    };
  }

  function zoomAt(anchor: { x: number; y: number }, factor: number) {
    setView(current => {
      const scale = WIDTH / current.w;
      const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor));
      const w = WIDTH / next;
      const h = HEIGHT / next;
      const xFraction = (anchor.x - current.x) / current.w;
      const yFraction = (anchor.y - current.y) / current.h;
      return {
        w,
        h,
        x: Math.min(Math.max(anchor.x - xFraction * w, 0), WIDTH - w),
        y: Math.min(Math.max(anchor.y - yFraction * h, 0), HEIGHT - h),
      };
    });
  }

  return (
    <div className="relative overflow-hidden border border-gray-200 bg-[#fbfaf9]">
      <svg
        ref={svgRef}
        viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
        className={`block h-auto w-full select-none ${panning ? 'cursor-grabbing' : zoomedIn ? 'cursor-grab' : 'cursor-crosshair'}`}
        aria-label="Delivery-zone demand heat map"
        onWheel={event => {
          if (!event.ctrlKey && !event.metaKey && !event.shiftKey) return;
          zoomAt(toViewBox(event), event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP);
        }}
        onMouseDown={event => setPanning(toViewBox(event))}
        onMouseUp={() => setPanning(null)}
        onDoubleClick={() => setView({ x: 0, y: 0, w: WIDTH, h: HEIGHT })}
        onMouseLeave={() => { setHovered(null); setPanning(null); }}
        onMouseMove={event => {
          const box = event.currentTarget.getBoundingClientRect();
          setCursor({ x: event.clientX - box.left, y: event.clientY - box.top, width: box.width });
          if (!panning) return;
          const at = toViewBox(event);
          setPanning(at);
          setView(current => ({
            ...current,
            x: Math.min(Math.max(current.x - (at.x - panning.x), 0), WIDTH - current.w),
            y: Math.min(Math.max(current.y - (at.y - panning.y), 0), HEIGHT - current.h),
          }));
        }}
      >
        {data.zones.map(zone => (
          <path
            key={zone.id}
            d={zonePath(zone.geometry, projection)}
            fill={heatColor(Math.sqrt(zone[metric] / maxValue))}
            fillOpacity={0.12 + Math.sqrt(zone[metric] / maxValue) * 0.7}
            stroke={hovered?.id === zone.id ? '#374151' : '#9ca3af'}
            strokeWidth={hovered?.id === zone.id ? '1.5' : '0.9'}
            strokeLinejoin="round"
            className="cursor-crosshair transition-all duration-200"
            onMouseEnter={() => setHovered(zone)}
          />
        ))}
      </svg>
      <div className="absolute right-2 top-2 z-10 flex flex-col gap-1">
        {[
          ['+', () => zoomAt({ x: view.x + view.w / 2, y: view.y + view.h / 2 }, ZOOM_STEP)],
          ['−', () => zoomAt({ x: view.x + view.w / 2, y: view.y + view.h / 2 }, 1 / ZOOM_STEP)],
        ].map(([label, onClick]) => (
          <button
            key={label as string}
            type="button"
            onClick={onClick as () => void}
            className="h-6 w-6 border border-gray-300 bg-white/90 text-sm leading-none text-gray-600 hover:bg-white"
          >
            {label as string}
          </button>
        ))}
        {zoomedIn && (
          <button
            type="button"
            onClick={() => setView({ x: 0, y: 0, w: WIDTH, h: HEIGHT })}
            className="h-6 w-6 border border-gray-300 bg-white/90 text-[10px] leading-none text-gray-500 hover:bg-white"
            title="Fit all delivery zones"
          >
            ⤢
          </button>
        )}
      </div>
      {hovered && (
        <div
          className="pointer-events-none absolute z-10 border border-gray-200 bg-white/95 px-3 py-2 shadow-sm backdrop-blur"
          style={{
            left: Math.max(0, Math.min(cursor.x + 14, cursor.width - 220)),
            top: Math.max(0, cursor.y - 10),
          }}
        >
          <p className="text-[10px] font-body uppercase tracking-widest text-gray-400">{hovered.name}</p>
          {!showChannelBreakdown ? (
            <>
              <p className="mt-0.5 text-sm font-body text-gray-800">{hovered.customer_count} customers · {hovered.order_count} orders</p>
              <p className="text-xs font-body text-gray-500">AED {hovered.revenue.toLocaleString(undefined, { maximumFractionDigits: 0 })} revenue · AED {hovered.aov.toFixed(0)} AOV</p>
            </>
          ) : (
            <div className="mt-1.5 min-w-48 space-y-1">
              {Object.entries(hovered.channel_breakdown)
                .sort(([, left], [, right]) => right[metric] - left[metric])
                .map(([channel, values]) => (
                  <div key={channel} className="flex items-baseline justify-between gap-5 text-xs font-body">
                    <span className="capitalize text-gray-500">{channel.replaceAll('_', ' ')}</span>
                    <span className="text-gray-800">
                      {metric === 'customer_count'
                        ? `${values.customer_count} customers`
                        : metric === 'revenue'
                          ? `AED ${values.revenue.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                          : `AED ${values.aov.toFixed(0)}`}
                    </span>
                  </div>
                ))}
              {!Object.keys(hovered.channel_breakdown).length && (
                <p className="text-xs font-body text-gray-500">No geocoded orders in this zone.</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
