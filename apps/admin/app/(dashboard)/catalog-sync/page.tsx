'use client';

import { useCallback, useEffect, useState } from 'react';

import { ApiError, catalogSyncApi } from '@/lib/api';
import { branchesApi } from '@/lib/pos-api';
import type { Branch } from '@/lib/pos-types';
import type { Schemas } from '@mm/types';
import { Badge, Button, LoadError, Select, Spinner } from '@/components/ui';
import { useToast } from '@/components/ui/feedback';

type Status = Schemas['CatalogSyncStatus'];
type Report = Schemas['BranchDriftReport'];

interface Delta {
  kind: string;
  action: string;
  entity: string;
  mm_value?: string | null;
  channel_value?: string | null;
  detail?: string | null;
}
interface Diff {
  target: string;
  total: number;
  summary?: Record<string, number>;
  deltas: Delta[];
}
interface TargetReport {
  menu?: Diff | null;
  hours?: Diff | null;
  error?: string;
}

const ACTION_VARIANT: Record<string, 'success' | 'danger' | 'warning' | 'info' | 'neutral'> = {
  add: 'success',
  delete: 'danger',
  update: 'warning',
  info: 'info',
};

function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function DeltaTable({ title, diff }: { title: string; diff?: Diff | null }) {
  if (!diff) {
    return (
      <div className="text-sm text-gray-400 py-2">
        {title}: <span className="italic">no snapshot read yet</span>
      </div>
    );
  }
  if (diff.total === 0) {
    return (
      <div className="text-sm py-2">
        {title}: <Badge variant="success">in sync</Badge>
      </div>
    );
  }
  return (
    <div className="py-2">
      <div className="text-sm font-medium mb-2">
        {title}: <span className="text-gray-500">{diff.total} difference(s)</span>
      </div>
      <div className="overflow-x-auto border border-gray-200 rounded-sm">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
            <tr>
              <th className="text-left px-3 py-2">Action</th>
              <th className="text-left px-3 py-2">What</th>
              <th className="text-left px-3 py-2">Entity</th>
              <th className="text-left px-3 py-2">MM</th>
              <th className="text-left px-3 py-2">Channel</th>
            </tr>
          </thead>
          <tbody>
            {diff.deltas.map((d, i) => (
              <tr key={i} className="border-t border-gray-100">
                <td className="px-3 py-2">
                  <Badge variant={ACTION_VARIANT[d.action] ?? 'neutral'}>{d.action}</Badge>
                </td>
                <td className="px-3 py-2 text-gray-600">{d.kind.replace(/_/g, ' ')}</td>
                <td className="px-3 py-2 font-medium">{d.entity}</td>
                <td className="px-3 py-2 text-gray-500">{d.mm_value ?? '—'}</td>
                <td className="px-3 py-2 text-gray-500">{d.channel_value ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function CatalogSyncPage() {
  const toast = useToast();
  const [status, setStatus] = useState<Status | null>(null);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [branchId, setBranchId] = useState<string>('');
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [driftLoading, setDriftLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadBase = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [st, br] = await Promise.all([catalogSyncApi.status(), branchesApi.list()]);
      setStatus(st);
      setBranches(br);
      if (br.length && !branchId) setBranchId(br[0].id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Failed to load catalog sync status');
    } finally {
      setLoading(false);
    }
  }, [branchId]);

  useEffect(() => {
    loadBase();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadDrift = useCallback(async (id: string) => {
    if (!id) return;
    setDriftLoading(true);
    try {
      setReport(await catalogSyncApi.drift(id));
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Failed to load drift');
    } finally {
      setDriftLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    if (branchId) loadDrift(branchId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [branchId]);

  const onRefresh = async () => {
    if (!branchId) return;
    setRefreshing(true);
    try {
      await catalogSyncApi.refresh(branchId);
      toast.success('Live read complete');
      await loadDrift(branchId);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Refresh failed');
    } finally {
      setRefreshing(false);
    }
  };

  if (loading) return <div className="p-8 flex justify-center"><Spinner /></div>;
  if (error) return <div className="p-8"><LoadError message={error} onRetry={loadBase} /></div>;

  const targets = report?.targets as Record<string, TargetReport> | undefined;

  return (
    <div className="p-4 md:p-6 max-w-5xl">
      <div className="flex items-start justify-between mb-4 gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-display">Catalog Sync</h1>
          <p className="text-sm text-gray-500 mt-1">
            Drift between MM&apos;s flagged catalogue &amp; hours and each integrator. Read-only —
            writes are gated and land in a later phase.
          </p>
        </div>
        <Button onClick={onRefresh} loading={refreshing} disabled={!status?.read_enabled} variant="secondary">
          Refresh live read
        </Button>
      </div>

      {status && (
        <div className="flex flex-wrap gap-2 items-center mb-5 text-sm">
          <Badge variant={status.read_enabled ? 'success' : 'neutral'}>
            reads {status.read_enabled ? 'on' : 'off'}
          </Badge>
          <Badge variant={status.write_enabled ? 'warning' : 'neutral'}>
            writes {status.write_enabled ? 'on (dry-run)' : 'off'}
          </Badge>
          <Badge variant={status.enforce_price_parity ? 'info' : 'neutral'}>
            price parity {status.enforce_price_parity ? 'enforced' : 'off'}
          </Badge>
          {!status.read_enabled && (
            <span className="text-gray-400">
              Live reads are disabled (CATALOG_SYNC_READ_ENABLED). Showing stored snapshots only.
            </span>
          )}
        </div>
      )}

      <div className="max-w-xs mb-6">
        <Select
          label="Branch"
          value={branchId}
          onChange={(e) => setBranchId(e.target.value)}
          options={branches.map((b) => ({ value: b.id, label: b.name }))}
        />
      </div>

      {driftLoading ? (
        <div className="py-8 flex justify-center"><Spinner /></div>
      ) : !targets ? (
        <p className="text-sm text-gray-400">Select a branch to see its drift.</p>
      ) : (
        <div className="space-y-4">
          {Object.entries(targets).map(([target, data]) => (
            <div key={target} className="border border-gray-200 rounded-sm p-4">
              <h2 className="text-lg font-display mb-1">{titleCase(target)}</h2>
              {data.error ? (
                <div className="text-sm text-red-600">{data.error}</div>
              ) : (
                <>
                  <DeltaTable title="Menu" diff={data.menu} />
                  <DeltaTable title="Hours" diff={data.hours} />
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
