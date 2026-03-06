'use client';

import { useMemo, useRef, useState } from 'react';
import { importApi, exportApi, ApiError } from '@/lib/api';
import type { ImportResult } from '@/lib/types';
import { Button } from '@/components/ui';
import { useLanguages } from '@/hooks/useLanguages';

interface ImportSection {
  key: keyof typeof importApi;
  exportKey: string;
  title: string;
  description: string;
  columns: string;
}

function ImportCard({ section }: { section: ImportSection }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null;
    setSelectedFile(file);
    setResult(null);
    setError('');
  };

  const handleImport = async () => {
    if (!selectedFile) return;
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const fn = importApi[section.key] as (file: File) => Promise<ImportResult>;
      const res = await fn(selectedFile);
      setResult(res);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Import failed. Check the file format.');
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      await exportApi.download(section.exportKey);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Export failed.');
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="bg-white border border-gray-200 p-5">
      <div className="flex items-start justify-between mb-1">
        <h3 className="font-display text-base text-gray-800">{section.title}</h3>
        <Button variant="ghost" size="sm" loading={exporting} onClick={handleExport}>
          <span className="material-icons text-[14px]">download</span>
          Export
        </Button>
      </div>
      <p className="text-xs text-gray-500 font-body mb-1">{section.description}</p>
      <p className="text-[11px] text-gray-400 font-body mb-4">
        Expected columns: <span className="text-gray-600">{section.columns}</span>
      </p>

      <div className="flex items-center gap-3 mb-4">
        <input
          ref={fileRef}
          type="file"
          accept=".csv"
          onChange={handleFileChange}
          className="text-xs font-body text-gray-600 file:mr-3 file:py-1.5 file:px-3 file:border file:border-gray-300 file:text-xs file:font-body file:text-gray-700 file:bg-gray-50 hover:file:bg-gray-100"
        />
        <Button
          onClick={handleImport}
          disabled={!selectedFile || loading}
          loading={loading}
          size="sm"
        >
          Import
        </Button>
      </div>

      {error && (
        <div className="text-xs text-red-600 bg-red-50 border border-red-200 px-3 py-2 font-body">
          {error}
        </div>
      )}

      {result && (
        <div className="bg-gray-50 border border-gray-200 p-3 text-xs font-body space-y-1">
          <div className="flex gap-4">
            <span className="text-green-700">Created: <strong>{result.created}</strong></span>
            <span className="text-blue-700">Updated: <strong>{result.updated}</strong></span>
            <span className="text-gray-500">Skipped: <strong>{result.skipped}</strong></span>
          </div>
          {result.errors.length > 0 && (
            <div className="mt-2">
              <p className="text-red-600 font-medium mb-1">Errors ({result.errors.length}):</p>
              <ul className="space-y-0.5 max-h-40 overflow-y-auto">
                {result.errors.map((e, i) => (
                  <li key={i} className="text-red-500">Row {e.row}: {e.message}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ImportPage() {
  const { languages } = useLanguages();

  const sections = useMemo<ImportSection[]>(() => {
    const langCols = (fields: string[]) =>
      languages.flatMap(l => fields.map(f => `${f}_${l.code}`)).join(', ');

    const catLang = langCols(['name', 'description']);
    const prodLang = langCols(['name', 'description']);
    const modLang = langCols(['name']);
    const optLang = langCols(['name']);

    return [
      {
        key: 'categories',
        exportKey: 'categories',
        title: '1. Categories',
        description: 'Import product categories from Foodics Categories Export.',
        columns: ['id', 'name', catLang, 'reference', 'image'].filter(Boolean).join(', '),
      },
      {
        key: 'products',
        exportKey: 'products',
        title: '2. Products',
        description: 'Import products. Categories must be imported first.',
        columns: ['id', 'name', 'sku', 'category_reference', 'price', 'description', 'image', prodLang, 'is_active', 'is_stock_product', 'calories', 'preparation_time'].filter(Boolean).join(', '),
      },
      {
        key: 'modifiers',
        exportKey: 'modifiers',
        title: '3. Modifiers',
        description: 'Import modifier groups (e.g. "Size", "Your Choice of Quantity").',
        columns: ['id', 'reference', 'name', modLang].filter(Boolean).join(', '),
      },
      {
        key: 'modifierOptions',
        exportKey: 'modifier-options',
        title: '4. Modifier Options',
        description: 'Import modifier options. Modifiers must be imported first.',
        columns: ['id', 'modifier_reference', 'name', 'sku', 'price', optLang, 'is_active'].filter(Boolean).join(', '),
      },
      {
        key: 'productModifiers',
        exportKey: 'product-modifiers',
        title: '5. Product Modifiers',
        description: 'Link modifiers to products. Products and Modifiers must be imported first.',
        columns: 'product_sku, modifier_reference, minimum_options, maximum_options, free_options, unique_options',
      },
    ];
  }, [languages]);

  return (
    <div>
      <div className="mb-6">
        <h1 className="font-display text-2xl text-gray-800">Import / Export</h1>
        <p className="text-xs text-gray-400 font-body mt-0.5">
          Import product catalog from Foodics CSV exports. Import in order: Categories → Products → Modifiers → Modifier Options → Product Modifiers.
        </p>
      </div>

      <div className="space-y-4">
        {sections.map(section => (
          <ImportCard key={section.key} section={section} />
        ))}
      </div>
    </div>
  );
}
