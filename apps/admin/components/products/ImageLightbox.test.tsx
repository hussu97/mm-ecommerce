import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ImageLightbox } from './ImageLightbox';

vi.mock('next/image', () => ({
  // eslint-disable-next-line @next/next/no-img-element
  default: (props: { src: string; alt: string }) => <img src={props.src} alt={props.alt} />,
}));

const URLS = ['https://img/a.jpg', 'https://img/b.jpg', 'https://img/c.jpg'];

describe('ImageLightbox', () => {
  it('renders nothing while closed', () => {
    const { container } = render(<ImageLightbox urls={URLS} index={null} onIndexChange={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the selected image full size with its position', () => {
    render(<ImageLightbox urls={URLS} index={1} onIndexChange={() => {}} />);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('2 / 3')).toBeInTheDocument();
    expect(document.querySelector('img')?.getAttribute('src')).toBe(URLS[1]);
    expect(screen.getByText('Open original').getAttribute('href')).toBe(URLS[1]);
  });

  it('steps with the arrows and closes on Escape or the backdrop', () => {
    const onIndexChange = vi.fn();
    render(<ImageLightbox urls={URLS} index={1} onIndexChange={onIndexChange} />);
    fireEvent.keyDown(window, { key: 'ArrowRight' });
    expect(onIndexChange).toHaveBeenLastCalledWith(2);
    fireEvent.keyDown(window, { key: 'ArrowLeft' });
    expect(onIndexChange).toHaveBeenLastCalledWith(0);
    fireEvent.click(screen.getByLabelText('Next image'));
    expect(onIndexChange).toHaveBeenLastCalledWith(2);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onIndexChange).toHaveBeenLastCalledWith(null);
    onIndexChange.mockClear();
    fireEvent.click(screen.getByRole('dialog'));
    expect(onIndexChange).toHaveBeenCalledWith(null);
  });

  it('does not close when the image itself is clicked', () => {
    const onIndexChange = vi.fn();
    render(<ImageLightbox urls={URLS} index={0} onIndexChange={onIndexChange} />);
    fireEvent.click(document.querySelector('img')!);
    expect(onIndexChange).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('Previous image')).toBeNull();
  });
});
