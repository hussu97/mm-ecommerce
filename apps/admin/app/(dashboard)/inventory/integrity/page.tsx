'use client';

import { useEffect, useState } from 'react';
import { inventoryApi, type BranchInventorySettings } from '@/lib/pos-api';
import { Button } from '@/components/ui';
import { DataTable } from '@/components/ui/DataTable';
import { formatQuantity } from '@/lib/utils';
import { BranchFilter } from '../_shared';

export default function IntegrityPage() {
  const [branchId, setBranchId] = useState('');
  const [rows, setRows] = useState<Array<{ item_id: string; cached_quantity: string; ledger_quantity: string; cached_average_cost: string; ledger_average_cost: string }>>([]);
  const [settings, setSettings] = useState<BranchInventorySettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [checked, setChecked] = useState(false);
  const check = async (apply = false) => { if (!branchId) return; setLoading(true); try { setRows(await (apply ? inventoryApi.rebuildProjection(branchId) : inventoryApi.projectionDrift(branchId))); setChecked(true); } finally { setLoading(false); } };
  useEffect(() => { setChecked(false); setRows([]); if (branchId) void inventoryApi.branchSettings(branchId).then(setSettings); else setSettings(null); }, [branchId]);
  const saveSettings = async () => { if (!settings) return; setLoading(true); try { setSettings(await inventoryApi.updateBranchSettings(settings.branch_id, settings)); } finally { setLoading(false); } };
  return <div className="max-w-5xl space-y-4"><BranchFilter value={branchId} onChange={setBranchId} />{settings && <div className="border border-gray-200 p-4 space-y-3"><div><h3 className="font-medium text-gray-800">Branch inventory rollout</h3><p className="text-xs text-gray-500">Inventory and sales consumption cannot be enabled until a manager-approved opening count records the go-live watermark.</p></div><div className="flex flex-wrap gap-5 text-sm"><label className="flex gap-2"><input type="checkbox" checked={settings.inventory_enabled} disabled={!settings.go_live_at} onChange={(event) => setSettings({ ...settings, inventory_enabled: event.target.checked })} />Inventory enabled</label><label className="flex gap-2"><input type="checkbox" checked={settings.sales_consumption_enabled} disabled={!settings.go_live_at} onChange={(event) => setSettings({ ...settings, sales_consumption_enabled: event.target.checked })} />Sales consumption</label><label className="flex gap-2"><input type="checkbox" checked={settings.production_enabled} onChange={(event) => setSettings({ ...settings, production_enabled: event.target.checked })} />Production</label><label className="flex gap-2"><input type="checkbox" checked={settings.validation_mode} onChange={(event) => setSettings({ ...settings, validation_mode: event.target.checked })} />Validation mode</label><label className="flex gap-2"><input type="checkbox" checked={settings.allow_negative_stock} onChange={(event) => setSettings({ ...settings, allow_negative_stock: event.target.checked })} />Allow negative</label></div><div className="flex items-center justify-between text-xs text-gray-500"><span>{settings.go_live_at ? `Opening count posted ${new Date(settings.go_live_at).toLocaleString()} · sequence ${settings.go_live_sequence}` : 'Awaiting opening count'}</span><Button size="sm" onClick={() => void saveSettings()} loading={loading}>Save settings</Button></div></div>}<div className="flex gap-2"><Button onClick={() => void check()} loading={loading} disabled={!branchId}>Preview ledger drift</Button><Button variant="outline" onClick={() => void check(true)} disabled={!branchId || loading}>Rebuild cache</Button></div><p className="text-sm text-gray-500">Rebuild replays closed ledger rows in posting-sequence order under the branch inventory lock.</p>{checked && rows.length === 0 ? <div className="border border-green-200 bg-green-50 p-4 text-sm text-green-800">No projection drift found.</div> : rows.length > 0 ? <DataTable rows={rows} rowKey={(row) => row.item_id} columns={[
    { header: 'Item', render: (row) => row.item_id }, { header: 'Cached qty', render: (row) => formatQuantity(row.cached_quantity) }, { header: 'Ledger qty', render: (row) => formatQuantity(row.ledger_quantity) }, { header: 'Cached cost', render: (row) => row.cached_average_cost }, { header: 'Ledger cost', render: (row) => row.ledger_average_cost },
  ]} /> : null}</div>;
}
