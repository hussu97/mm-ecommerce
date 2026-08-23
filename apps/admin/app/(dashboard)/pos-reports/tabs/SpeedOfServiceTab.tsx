'use client';

import { posReportsApi } from '@/lib/pos-api';
import { useReport, windowKey, Panel, Stat } from './_shared';
import type { SpeedOfServiceReport } from '@/lib/pos-types';
import type { Window } from '../report-window';


/**
 * Kitchen timings.
 *
 * Outstanding tickets sit beside the averages on purpose: each average covers
 * only the tickets that reached that stage, so a fast prep time with a long
 * queue behind it would otherwise read as good service.
 */
export function SpeedOfServiceTab({ window }: { window: Window }) {
  const { data, loading, error } = useReport<SpeedOfServiceReport>(
    () => posReportsApi.speedOfService(window),
    windowKey(window),
  );

  return (
    <Panel loading={loading} error={error} empty={!data?.tickets}>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        <Stat label="Tickets" value={String(data?.tickets ?? 0)} />
        <Stat
          label="Completed"
          value={String(data?.completed ?? 0)}
          hint={`${data?.outstanding ?? 0} still open`}
        />
        <Stat label="Slowest ticket" value={`${data?.slowest_ticket_minutes ?? 0} min`} />
        <Stat
          label="Acknowledge"
          value={`${data?.avg_acknowledge_minutes ?? 0} min`}
          hint="sent → started"
        />
        <Stat label="Prep" value={`${data?.avg_prep_minutes ?? 0} min`} hint="started → ready" />
        <Stat label="Total" value={`${data?.avg_total_minutes ?? 0} min`} hint="sent → ready" />
      </div>
    </Panel>
  );
}
