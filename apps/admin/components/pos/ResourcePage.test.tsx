import { act, render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ResourcePage, type ColumnDef, type FieldDef } from './ResourcePage';

// Characterisation tests: they pin the chrome behaviour (search, pagination,
// sort, empty/loading, and that New/Edit/Delete open their flows) so the
// refactor that moves the chrome into <ListPage> cannot change it unseen.
// DataTable renders both a desktop <table> and a mobile card list, so a cell's
// text appears twice in jsdom — assertions scope to the <table> or count all.

interface Widget {
  id: string;
  name: string;
}

const COLUMNS: ColumnDef<Widget>[] = [
  { header: 'Name', priority: 'primary', sortable: true, sortAccessor: (w) => w.name, render: (w) => w.name },
];
const FIELDS: FieldDef[] = [{ name: 'name', label: 'Name', required: true }];

function makeRows(n: number): Widget[] {
  return Array.from({ length: n }, (_, i) => ({ id: `w${i}`, name: `Widget ${String(i).padStart(3, '0')}` }));
}

function table() {
  return within(document.querySelector('table') as HTMLElement);
}

describe('ResourcePage chrome', () => {
  it('renders rows after load, with title and description', async () => {
    render(
      <ResourcePage<Widget>
        title="Widgets"
        description="All the widgets"
        columns={COLUMNS}
        fields={FIELDS}
        load={async () => makeRows(3)}
      />,
    );
    expect(screen.getByText('Widgets')).toBeInTheDocument();
    expect(screen.getByText('All the widgets')).toBeInTheDocument();
    await waitFor(() => expect(table().getByText('Widget 000')).toBeInTheDocument());
    expect(table().getByText('Widget 002')).toBeInTheDocument();
  });

  it('filters by searchKeys', async () => {
    render(
      <ResourcePage<Widget>
        title="Widgets"
        columns={COLUMNS}
        fields={FIELDS}
        searchKeys={['name']}
        load={async () => [
          { id: 'a', name: 'Apple' },
          { id: 'b', name: 'Banana' },
        ]}
      />,
    );
    await waitFor(() => expect(table().getByText('Apple')).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText('Search…'), { target: { value: 'ban' } });
    await waitFor(() => expect(table().queryByText('Apple')).not.toBeInTheDocument());
    expect(table().getByText('Banana')).toBeInTheDocument();
  });

  it('paginates when `paginated`, and Next advances the page', async () => {
    render(
      <ResourcePage<Widget>
        title="Widgets"
        columns={COLUMNS}
        fields={FIELDS}
        paginated
        load={async () => makeRows(60)}
      />,
    );
    await waitFor(() => expect(table().getByText('Widget 000')).toBeInTheDocument());
    // Default page size 50: first page shows 000..049, not 050+.
    expect(table().queryByText('Widget 050')).not.toBeInTheDocument();
    expect(screen.getByText(/Page 1 of 2 · 60 widgets/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    await waitFor(() => expect(table().getByText('Widget 050')).toBeInTheDocument());
    expect(table().queryByText('Widget 000')).not.toBeInTheDocument();
  });

  it('shows the empty message when there are no rows', async () => {
    render(
      <ResourcePage<Widget>
        title="Widgets"
        columns={COLUMNS}
        fields={FIELDS}
        emptyMessage="No widgets yet."
        load={async () => []}
      />,
    );
    await waitFor(() => expect(screen.getByText('No widgets yet.')).toBeInTheDocument());
  });

  it('opens the create modal and calls create', async () => {
    const create = vi.fn(async (d: Record<string, unknown>) => ({ id: 'new', name: String(d.name) }));
    render(
      <ResourcePage<Widget>
        title="Widgets"
        columns={COLUMNS}
        fields={FIELDS}
        load={async () => makeRows(1)}
        create={create}
      />,
    );
    await waitFor(() => expect(table().getByText('Widget 000')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'New' }));
    expect(screen.getByText('New Widget')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Fresh' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(create).toHaveBeenCalledWith({ name: 'Fresh' }));
  });

  it('opens delete confirmation and calls remove', async () => {
    const remove = vi.fn(async () => {});
    render(
      <ResourcePage<Widget>
        title="Widgets"
        columns={COLUMNS}
        fields={FIELDS}
        load={async () => makeRows(1)}
        remove={remove}
      />,
    );
    await waitFor(() => expect(table().getByText('Widget 000')).toBeInTheDocument());
    fireEvent.click(table().getAllByRole('button', { name: 'Delete' })[0]);
    expect(screen.getByText('Confirm delete')).toBeInTheDocument();
    // The confirm dialog's own Delete button.
    const dialogDelete = screen.getAllByRole('button', { name: 'Delete' }).at(-1)!;
    fireEvent.click(dialogDelete);
    await waitFor(() => expect(remove).toHaveBeenCalledWith('w0'));
  });
});
