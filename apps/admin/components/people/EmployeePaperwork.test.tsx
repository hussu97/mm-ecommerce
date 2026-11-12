import { fireEvent, render, screen } from '@testing-library/react';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import type { EmployeeDocument, EmployeeDocumentTypeSpec, EmployeeFile } from '@/lib/people-api';
import { DocumentCard, OtherFiles, PaperworkSummary, paperStatus, relativeDays } from './EmployeePaperwork';

// Noon in Dubai on 12 Nov 2026, so "today" is unambiguous.
beforeAll(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-11-12T08:00:00Z'));
});
afterAll(() => vi.useRealTimers());

const spec: EmployeeDocumentTypeSpec = {
  doc_type: 'emirates_id',
  label: 'Emirates ID',
  number_label: 'ID number',
  authority_label: 'Issuing authority',
  expiry_tracked: true,
  details: { card_number: 'Card number', occupation: 'Occupation' },
};

function doc(expiry: string, extra: Partial<EmployeeDocument> = {}): EmployeeDocument {
  return {
    id: `d-${expiry}`,
    employee_id: 'e1',
    doc_type: 'emirates_id',
    document_number: '784-1990-1234567-1',
    issue_date: null,
    expiry_date: expiry,
    issuing_authority: null,
    details: { card_number: '123456789' },
    notes: null,
    has_file: true,
    file_content_type: 'application/pdf',
    is_current: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...extra,
  };
}

function card(expiry: string, props: Partial<Parameters<typeof DocumentCard>[0]> = {}) {
  const handlers = { onView: vi.fn(), onEdit: vi.fn(), onDelete: vi.fn(), onRenew: vi.fn(), onCopy: vi.fn() };
  render(<DocumentCard spec={spec} current={doc(expiry)} history={[]} highlighted={false} {...handlers} {...props} />);
  return handlers;
}

describe('paperStatus', () => {
  it('uses the expiry popup thresholds', () => {
    expect(paperStatus(null).tone).toBe('missing');
    expect(paperStatus('2026-11-11')).toEqual({ tone: 'expired', label: 'Expired yesterday' });
    expect(paperStatus('2026-11-12')).toEqual({ tone: 'due', label: 'Expires today' });
    expect(paperStatus('2026-11-27').tone).toBe('due'); // 15 days: inside the warning
    expect(paperStatus('2026-11-28').tone).toBe('soon');
    expect(paperStatus('2027-01-12').tone).toBe('ok');
    expect(relativeDays('2026-10-31')).toBe('12 days ago');
    expect(relativeDays('2026-11-21')).toBe('in 9 days');
  });
});

describe('DocumentCard', () => {
  it('makes renewal the call to action only when it is due', () => {
    const h = card('2026-11-21');
    expect(screen.getByRole('button', { name: /Renew now/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add renewal' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Renew now/ }));
    expect(h.onRenew).toHaveBeenCalled();
    expect(screen.getByText('in 9 days')).toBeInTheDocument();
  });

  it('keeps renewal to a header icon when the document is valid', () => {
    const h = card('2028-01-01');
    expect(screen.queryByRole('button', { name: /Renew now/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add renewal' }));
    expect(h.onRenew).toHaveBeenCalled();
  });

  it('copies the number, hides details until asked, and lists earlier cycles', () => {
    const h = card('2028-01-01', {
      history: [doc('2025-01-01', { id: 'old', is_current: false, document_number: 'OLD-1' })],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Copy id number' }));
    expect(h.onCopy).toHaveBeenCalledWith('784-1990-1234567-1');

    expect(screen.queryByText('123456789')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Details (1)' }));
    expect(screen.getByText('123456789')).toBeInTheDocument();

    expect(screen.queryByText('OLD-1')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /1 previous/ }));
    expect(screen.getByText('OLD-1')).toBeInTheDocument();
  });

  it('disables viewing when there is no scan', () => {
    render(
      <DocumentCard
        spec={spec}
        current={doc('2028-01-01', { has_file: false })}
        history={[]}
        highlighted={false}
        onView={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRenew={vi.fn()}
        onCopy={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'No scan on file' })).toBeDisabled();
  });
});

describe('PaperworkSummary', () => {
  const visa = { ...spec, doc_type: 'visa', label: 'Residence visa' };

  it('jumps to a document on file and adds a missing one', () => {
    const onOpen = vi.fn();
    const onAdd = vi.fn();
    render(
      <PaperworkSummary
        states={[{ spec, current: doc('2026-11-21') }, { spec: visa }]}
        onOpen={onOpen}
        onAdd={onAdd}
      />,
    );
    expect(screen.getByText('2 need attention')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('status-emirates_id'));
    expect(onOpen).toHaveBeenCalledWith('emirates_id');
    fireEvent.click(screen.getByTestId('status-visa'));
    expect(onAdd).toHaveBeenCalledWith('visa');
  });

  it('says when nothing is on file yet, and when all is well', () => {
    const { rerender } = render(<PaperworkSummary states={[{ spec }]} onOpen={vi.fn()} onAdd={vi.fn()} />);
    expect(screen.getByText(/Nothing on file yet/)).toBeInTheDocument();
    rerender(<PaperworkSummary states={[{ spec, current: doc('2028-01-01') }]} onOpen={vi.fn()} onAdd={vi.fn()} />);
    expect(screen.getByText('Everything is on file and valid')).toBeInTheDocument();
  });
});

describe('OtherFiles', () => {
  const file = (n: number, title: string, category: string | null): EmployeeFile => ({
    id: `f${n}`,
    employee_id: 'e1',
    title,
    category,
    notes: null,
    original_filename: null,
    has_file: true,
    file_content_type: 'application/pdf',
    created_at: '2026-10-02T09:00:00Z',
    updated_at: '2026-10-02T09:00:00Z',
  });

  it('shows a type only when the name does not already say it', () => {
    render(
      <OtherFiles
        files={[file(1, 'Employment Contract', 'Employment contract'), file(2, 'Signed copy', 'NOC')]}
        onAdd={vi.fn()}
        onView={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.queryByText(/Employment contract ·/)).not.toBeInTheDocument();
    expect(screen.getByText(/NOC ·/)).toBeInTheDocument();
  });

  it('offers search once there are more than five, and filters by name or type', () => {
    const files = [1, 2, 3, 4, 5].map((n) => file(n, `Payslip ${n}`, 'Payslip'));
    files.push(file(6, 'Signed copy', 'NOC'));
    render(<OtherFiles files={files} onAdd={vi.fn()} onView={vi.fn()} onEdit={vi.fn()} onDelete={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Search other documents'), { target: { value: 'noc' } });
    expect(screen.getByText('Signed copy')).toBeInTheDocument();
    expect(screen.queryByText('Payslip 1')).not.toBeInTheDocument();
  });
});
