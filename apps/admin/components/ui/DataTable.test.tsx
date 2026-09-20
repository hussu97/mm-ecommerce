import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DataTable, RowAction, type DataColumn } from './DataTable';

interface Row {
  id: string;
  name: string;
  amount: string;
}

const COLUMNS: DataColumn<Row>[] = [
  { header: 'Name', priority: 'primary', render: r => r.name },
  { header: 'Amount', render: r => r.amount },
];

const ROWS: Row[] = [
  { id: 'a', name: 'Alpha', amount: '10' },
  { id: 'b', name: 'Beta', amount: '20' },
];

const table = () => within(document.querySelector('table') as HTMLElement);

describe('DataTable getRowHref', () => {
  it('renders every desktop cell as an anchor to the row href', () => {
    render(
      <DataTable<Row>
        rows={ROWS}
        rowKey={r => r.id}
        columns={COLUMNS}
        getRowHref={r => `/things/${r.id}`}
      />,
    );
    // The identity text and a non-identity cell both link to the same detail
    // page, so a plain click anywhere navigates and cmd/middle-click on any
    // cell opens the row in a new tab.
    const nameLink = table().getByText('Alpha').closest('a');
    const amountLink = table().getByText('10').closest('a');
    expect(nameLink).toHaveAttribute('href', '/things/a');
    expect(amountLink).toHaveAttribute('href', '/things/a');
  });

  it('keeps only the first cell of each row in the tab order', () => {
    render(
      <DataTable<Row>
        rows={ROWS}
        rowKey={r => r.id}
        columns={COLUMNS}
        getRowHref={r => `/things/${r.id}`}
      />,
    );
    // The first cell's link is focusable; the repeat links to the same place
    // are removed from the tab order.
    expect(table().getByText('Alpha').closest('a')).not.toHaveAttribute('tabindex', '-1');
    expect(table().getByText('10').closest('a')).toHaveAttribute('tabindex', '-1');
  });

  it('renders no anchors when getRowHref is absent', () => {
    render(<DataTable<Row> rows={ROWS} rowKey={r => r.id} columns={COLUMNS} />);
    expect(document.querySelector('table a')).toBeNull();
  });

  it('renders a RowAction with an href as a link, and without as a button', () => {
    render(
      <DataTable<Row>
        rows={[ROWS[0]]}
        rowKey={r => r.id}
        columns={COLUMNS}
        actions={r => <RowAction href={`/things/${r.id}/edit`}>Edit</RowAction>}
      />,
    );
    expect(table().getByText('Edit').closest('a')).toHaveAttribute(
      'href',
      '/things/a/edit',
    );
  });
});
