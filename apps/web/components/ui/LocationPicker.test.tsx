import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { LocationPicker } from './LocationPicker';

// ─── Mocks ────────────────────────────────────────────────────────────────────

const mockPanTo = vi.fn();
const mockSetZoom = vi.fn();
const mockUseMap = vi.fn(() => ({ panTo: mockPanTo, setZoom: mockSetZoom }));
const mockUseMapsLibrary = vi.fn();

vi.mock('@vis.gl/react-google-maps', () => ({
  APIProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Map: ({ children, onClick }: { children?: React.ReactNode; onClick?: (e: unknown) => void }) => (
    <div
      data-testid="map"
      onClick={() => onClick?.({ detail: { latLng: { lat: 10, lng: 20 } } })}
    >
      {children}
    </div>
  ),
  AdvancedMarker: ({ onDragEnd }: { onDragEnd?: (e: unknown) => void }) => (
    <div
      data-testid="marker"
      onMouseUp={() => onDragEnd?.({ latLng: { lat: () => 10, lng: () => 20 } })}
    />
  ),
  useMapsLibrary: () => mockUseMapsLibrary(),
  useMap: () => mockUseMap(),
}));

// ─── Types ────────────────────────────────────────────────────────────────────

type MockElement = HTMLDivElement & {
  setAttribute: ReturnType<typeof vi.fn>;
  addEventListener: ReturnType<typeof vi.fn>;
  removeEventListener: ReturnType<typeof vi.fn>;
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

let mockPlaceAutocompleteElement: MockElement;

function makeMockElement(): MockElement {
  const el = document.createElement('div') as MockElement;
  // Spy on setAttribute so we can assert it was called correctly
  vi.spyOn(el, 'setAttribute');
  vi.spyOn(el, 'addEventListener');
  vi.spyOn(el, 'removeEventListener');
  return el;
}

function renderLocationPicker(props: Partial<Parameters<typeof LocationPicker>[0]> = {}) {
  const onChange = vi.fn();
  render(<LocationPicker lat={null} lng={null} onChange={onChange} {...props} />);
  return { onChange };
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('LocationPicker', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockPlaceAutocompleteElement = makeMockElement();
    mockUseMapsLibrary.mockReturnValue({
      PlaceAutocompleteElement: vi.fn(() => mockPlaceAutocompleteElement),
    });
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  // ── No API key ──────────────────────────────────────────────────────────────

  describe('when API key is missing', () => {
    beforeEach(() => {
      vi.stubEnv('NEXT_PUBLIC_GOOGLE_MAPS_API_KEY', '');
    });

    it('renders the fallback message', () => {
      renderLocationPicker();
      expect(screen.getByText('Google Maps not configured')).toBeInTheDocument();
    });

    it('does not render the map', () => {
      renderLocationPicker();
      expect(screen.queryByTestId('map')).not.toBeInTheDocument();
    });
  });

  // ── With API key ────────────────────────────────────────────────────────────

  describe('when API key is present', () => {
    beforeEach(() => {
      vi.stubEnv('NEXT_PUBLIC_GOOGLE_MAPS_API_KEY', 'test-api-key');
    });

    it('renders the map', () => {
      renderLocationPicker();
      expect(screen.getByTestId('map')).toBeInTheDocument();
    });

    it('does not render a marker when no position is set', () => {
      renderLocationPicker();
      expect(screen.queryByTestId('marker')).not.toBeInTheDocument();
    });

    it('renders a marker when lat/lng are provided', () => {
      renderLocationPicker({ lat: 25.2, lng: 55.27 });
      expect(screen.getByTestId('marker')).toBeInTheDocument();
    });

    it('calls onChange when the map is clicked', () => {
      const { onChange } = renderLocationPicker();
      act(() => screen.getByTestId('map').click());
      expect(onChange).toHaveBeenCalledWith(10, 20);
    });
  });

  // ── PlaceAutocompleteElement ────────────────────────────────────────────────

  describe('PlaceAutocompleteElement', () => {
    beforeEach(() => {
      vi.stubEnv('NEXT_PUBLIC_GOOGLE_MAPS_API_KEY', 'test-api-key');
    });

    it('sets placeholder via setAttribute, not constructor option', () => {
      renderLocationPicker({ placeholder: 'Find a place' });
      expect(mockPlaceAutocompleteElement.setAttribute).toHaveBeenCalledWith(
        'placeholder',
        'Find a place',
      );
    });

    it('uses the default placeholder when none is provided', () => {
      renderLocationPicker();
      expect(mockPlaceAutocompleteElement.setAttribute).toHaveBeenCalledWith(
        'placeholder',
        'Search for a location…',
      );
    });

    it('does not pass inputPlaceholder to the PlaceAutocompleteElement constructor', () => {
      const PlaceAutocompleteElementSpy = vi.fn(() => mockPlaceAutocompleteElement);
      mockUseMapsLibrary.mockReturnValue({ PlaceAutocompleteElement: PlaceAutocompleteElementSpy });

      renderLocationPicker({ placeholder: 'Search…' });

      const [constructorArg] = PlaceAutocompleteElementSpy.mock.calls[0] ?? [{}];
      expect((constructorArg as Record<string, unknown>)?.inputPlaceholder).toBeUndefined();
    });

    it('pans the map and sets zoom on place selection', async () => {
      // Capture the handler registered by the component
      let capturedHandler: ((e: Event) => void) | null = null;
      mockPlaceAutocompleteElement.addEventListener.mockImplementation(
        (_: string, handler: EventListenerOrEventListenerObject) => {
          capturedHandler = handler as (e: Event) => void;
        },
      );

      renderLocationPicker();

      const mockPlace = {
        fetchFields: vi.fn().mockResolvedValue(undefined),
        location: { lat: () => 25.2, lng: () => 55.27 },
      };

      await act(async () => {
        capturedHandler?.(new CustomEvent('gmp-placeselect', { detail: { place: mockPlace } }));
      });

      expect(mockPlace.fetchFields).toHaveBeenCalledWith({ fields: ['location'] });
      expect(mockPanTo).toHaveBeenCalledWith({ lat: 25.2, lng: 55.27 });
      expect(mockSetZoom).toHaveBeenCalledWith(15);
    });

    it('does not call onChange when place has no location', async () => {
      let capturedHandler: ((e: Event) => void) | null = null;
      mockPlaceAutocompleteElement.addEventListener.mockImplementation(
        (_: string, handler: EventListenerOrEventListenerObject) => {
          capturedHandler = handler as (e: Event) => void;
        },
      );

      const { onChange } = renderLocationPicker();

      const mockPlace = {
        fetchFields: vi.fn().mockResolvedValue(undefined),
        location: null,
      };

      await act(async () => {
        capturedHandler?.(new CustomEvent('gmp-placeselect', { detail: { place: mockPlace } }));
      });

      expect(onChange).not.toHaveBeenCalled();
    });

    it('removes the event listener on unmount', () => {
      mockPlaceAutocompleteElement.addEventListener.mockImplementation(() => {});

      const { unmount } = render(
        <LocationPicker lat={null} lng={null} onChange={vi.fn()} />,
      );

      unmount();

      expect(mockPlaceAutocompleteElement.removeEventListener).toHaveBeenCalledWith(
        'gmp-placeselect',
        expect.any(Function),
      );
    });
  });
});
