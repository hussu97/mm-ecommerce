import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useFilePreview, type PreviewSource } from './FilePreview';

function Harness({ title, source }: { title: string; source: PreviewSource }) {
  const { preview, node } = useFilePreview();
  return (
    <>
      <button onClick={() => preview(title, source)}>open</button>
      {node}
    </>
  );
}

// Not a network URL: happy-dom loads an iframe's src for real.
const SIGNED = 'about:blank#employees/1/doc.pdf?X-Goog-Signature=abc';

describe('FilePreview', () => {
  it('signs on open and shows a PDF inline, not in a new tab', async () => {
    const open = vi.spyOn(window, 'open');
    const source = vi.fn().mockResolvedValue({
      url: SIGNED,
      download_url: `${SIGNED}&response-content-disposition=attachment`,
      filename: 'Adar - Passport.pdf',
      content_type: 'application/pdf',
    });
    render(<Harness title="Adar — Passport" source={source} />);
    expect(screen.queryByTestId('file-preview')).toBeNull();

    await act(async () => fireEvent.click(screen.getByText('open')));

    expect(source).toHaveBeenCalledOnce();
    expect(screen.getByTitle('Adar — Passport').getAttribute('src')).toBe(SIGNED);
    expect(screen.getByText('Adar - Passport.pdf')).toBeTruthy();
    expect(open).not.toHaveBeenCalled();
  });

  it('shows an image from a file in hand and closes on Escape', async () => {
    render(<Harness title="Logo" source={{ url: 'https://storage.googleapis.com/p/logos/x.png' }} />);
    await act(async () => fireEvent.click(screen.getByText('open')));
    expect(screen.getByAltText('Logo').getAttribute('src')).toContain('logos/x.png');
    // The name falls back to the URL's last segment.
    expect(screen.getByText('x.png')).toBeTruthy();

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByTestId('file-preview')).toBeNull();
  });

  it('reports a signing failure inside the pop-up', async () => {
    render(<Harness title="Visa" source={() => Promise.reject(new Error('boom'))} />);
    await act(async () => fireEvent.click(screen.getByText('open')));
    expect(screen.getByText('Could not open the file.')).toBeTruthy();
  });
});
