import { render, screen, within, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ListPage } from './ListPage';
import type { DataColumn } from './DataTable';

interface Row {
  id: string;
  name: string;
}
const COLUMNS: DataColumn<Row>[] = [{ header: 'Name', priority: 'primary', render: (r) => r.name }];
const table = () => within(document.querySelector('table') as HTMLElement);

describe('ListPage', () => {
  it('renders title, rows and a filter bar slot', () => {
    render(
      <ListPage<Row>
        title="Things"
        columns={COLUMNS}
        rows={[{ id: 'a', name: 'Alpha' }]}
        rowKey={(r) => r.id}
        filterBar={<div>my-filter-bar</div>}
      />,
    );
    expect(screen.getByText('Things')).toBeInTheDocument();
    expect(screen.getByText('my-filter-bar')).toBeInTheDocument();
    expect(table().getByText('Alpha')).toBeInTheDocument();
  });

  it('renders a LoadError with a working retry', () => {
    const onRetry = vi.fn();
    render(
      <ListPage<Row>
        title="Things"
        columns={COLUMNS}
        rows={[]}
        rowKey={(r) => r.id}
        loadError="boom"
        onRetry={onRetry}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('boom');
    fireEvent.click(screen.getByRole('button', { name: /try again/i }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('shows a spinner while loading and no table', () => {
    render(
      <ListPage<Row>
        title="Things"
        columns={COLUMNS}
        rows={[]}
        rowKey={(r) => r.id}
        loading
      />,
    );
    expect(document.querySelector('table')).toBeNull();
  });

  it('renders pagination from explicit props and wires Next', () => {
    const onPageChange = vi.fn();
    render(
      <ListPage<Row>
        title="Things"
        columns={COLUMNS}
        rows={[{ id: 'a', name: 'Alpha' }]}
        rowKey={(r) => r.id}
        pagination={{ page: 1, pages: 3, total: 120, perPage: 50, onPageChange, onPerPageChange: vi.fn(), label: 'things' }}
      />,
    );
    expect(screen.getByText(/Page 1 of 3 · 120 things/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });
});
