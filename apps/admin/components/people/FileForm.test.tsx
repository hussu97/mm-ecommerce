import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  fileCategories: vi.fn(),
  createFile: vi.fn(),
  updateFile: vi.fn(),
  uploadFile: vi.fn(),
  extractText: vi.fn(),
}));

vi.mock('@/lib/api', () => ({ ApiError: class ApiError extends Error {} }));
vi.mock('@/lib/people-api', () => ({
  employeesApi: {
    fileCategories: mocks.fileCategories,
    createFile: mocks.createFile,
    updateFile: mocks.updateFile,
    uploadFile: mocks.uploadFile,
  },
}));
vi.mock('@/lib/ocr/extract', () => ({ extractText: mocks.extractText }));

import { FileForm } from './FileForm';
import type { Employee, EmployeeFile } from '@/lib/people-api';

const employee = { id: 'e1', full_name: 'Juan Cruz' } as Employee;

function pick(file: File) {
  fireEvent.change(screen.getByTestId('employee-file'), { target: { files: [file] } });
}

describe('FileForm', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fileCategories.mockResolvedValue(['Employment contract', 'NOC']);
    mocks.createFile.mockResolvedValue({ id: 'f1' });
    mocks.uploadFile.mockResolvedValue({ id: 'f1' });
  });

  it('fills name and type from the document, creates the row, then uploads', async () => {
    mocks.extractText.mockResolvedValue({ text: 'EMPLOYMENT CONTRACT\nbetween…', source: 'pdf-text' });
    const onSaved = vi.fn();
    render(<FileForm employee={employee} onClose={() => {}} onSaved={onSaved} />);
    const file = new File(['%PDF'], 'scan0001.pdf', { type: 'application/pdf' });
    pick(file);
    await waitFor(() => expect(screen.getByLabelText('Type')).toHaveValue('Employment contract'));
    expect(screen.getByLabelText('Name *')).toHaveValue('Employment Contract');
    // The types already in use are offered.
    expect(document.querySelectorAll('#employee-file-categories option')).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(mocks.createFile).toHaveBeenCalledWith('e1', {
      title: 'Employment Contract',
      category: 'Employment contract',
      notes: null,
      original_filename: 'scan0001.pdf',
    });
    expect(mocks.uploadFile).toHaveBeenCalledWith('e1', 'f1', file);
  });

  it('never overwrites what the super-admin typed', async () => {
    let finish: (v: { text: string; source: string }) => void = () => {};
    mocks.extractText.mockReturnValue(new Promise((r) => (finish = r)));
    render(<FileForm employee={employee} onClose={() => {}} onSaved={() => {}} />);
    pick(new File(['%PDF'], 'x.pdf', { type: 'application/pdf' }));
    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'Visa copy' } });
    finish({ text: 'NO OBJECTION CERTIFICATE', source: 'pdf-text' });
    await waitFor(() => expect(screen.getByLabelText('Name *')).toHaveValue('No Objection Certificate'));
    expect(screen.getByLabelText('Type')).toHaveValue('Visa copy');
  });

  it('keeps the file name when the document cannot be read', async () => {
    mocks.extractText.mockRejectedValue(new Error('unreadable'));
    render(<FileForm employee={employee} onClose={() => {}} onSaved={() => {}} />);
    pick(new File(['x'], 'Offer_letter-2026.jpg', { type: 'image/jpeg' }));
    await waitFor(() => expect(mocks.extractText).toHaveBeenCalled());
    expect(screen.getByLabelText('Name *')).toHaveValue('Offer letter 2026');
    expect(screen.getByLabelText('Type')).toHaveValue('');
  });

  it('requires a file for a new record, and retries the upload on the same row', async () => {
    mocks.extractText.mockResolvedValue({ text: '', source: 'pdf-text' });
    mocks.uploadFile.mockRejectedValueOnce(new Error('offline'));
    const onSaved = vi.fn();
    render(<FileForm employee={employee} onClose={() => {}} onSaved={onSaved} />);
    fireEvent.change(screen.getByLabelText('Name *'), { target: { value: 'Contract' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Pick the file to upload.')).toBeInTheDocument();

    pick(new File(['%PDF'], 'c.pdf', { type: 'application/pdf' }));
    await waitFor(() => expect(mocks.extractText).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/did not upload/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(mocks.createFile).toHaveBeenCalledTimes(1);
    expect(mocks.updateFile).toHaveBeenCalledWith('e1', 'f1', expect.objectContaining({ title: 'Contract' }));
  });

  it('edits without reading the replacement file', async () => {
    const existing = { id: 'f9', title: 'Old', category: 'NOC', notes: null } as EmployeeFile;
    mocks.updateFile.mockResolvedValue(existing);
    const onSaved = vi.fn();
    render(<FileForm employee={employee} file={existing} onClose={() => {}} onSaved={onSaved} />);
    pick(new File(['%PDF'], 'new.pdf', { type: 'application/pdf' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(mocks.extractText).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Name *')).toHaveValue('Old');
    expect(mocks.uploadFile).toHaveBeenCalledWith('e1', 'f9', expect.any(File));
  });
});
