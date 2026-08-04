import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { LocationPicker } from './LocationPicker';

// ─── Mocks ────────────────────────────────────────────────────────────────────

const mockPanTo = vi.fn();
const mockSetZoom = vi.fn();
//: What the map currently has in view. `null` is the real pre-idle state — a
//: freshly mounted Google map has no bounds yet — and the picker has to treat
//: that as "cannot tell, so centre on the pin".
let mockBounds: { contains: (p: unknown) => boolean } | null = null;
const mockGetBounds = vi.fn(() => mockBounds);
const mockUseMap = vi.fn(() => ({
  panTo: mockPanTo,
  setZoom: mockSetZoom,
  getBounds: mockGetBounds,
}));
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
    mockBounds = null;
    mockPlaceAutocompleteElement = makeMockElement();
    mockUseMapsLibrary.mockReturnValue({
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      PlaceAutocompleteElement: vi.fn(function (this: any) { return mockPlaceAutocompleteElement; }),
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
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const PlaceAutocompleteElementSpy = vi.fn(function (this: any) { return mockPlaceAutocompleteElement; });
      mockUseMapsLibrary.mockReturnValue({ PlaceAutocompleteElement: PlaceAutocompleteElementSpy });

      renderLocationPicker({ placeholder: 'Search…' });

      const calls = PlaceAutocompleteElementSpy.mock.calls as unknown[][];
      const constructorArg = (calls[0]?.[0] ?? {}) as Record<string, unknown>;
      expect(constructorArg).toMatchObject({ includedRegionCodes: ['AE'] });
      expect(constructorArg.inputPlaceholder).toBeUndefined();
    });

    it('pans the map and sets zoom on place selection', async () => {
      // Capture the handler registered by the component
      let capturedHandler: ((e: Event) => void) | null = null;
      mockPlaceAutocompleteElement.addEventListener.mockImplementation(
        (_: string, handler: EventListenerOrEventListenerObject) => {
          capturedHandler = handler as (e: Event) => void;
        },
      );

      const { onChange } = renderLocationPicker();

      expect(mockPlaceAutocompleteElement.addEventListener).toHaveBeenCalledWith(
        'gmp-select',
        expect.any(Function),
      );

      const mockPlace = {
        fetchFields: vi.fn().mockResolvedValue(undefined),
        displayName: 'Melting Moments Cakes',
        formattedAddress: '21 Arab Club Street - Al MajazAl Majaz 3 - ضاحيةالمجاز - المجازAl Majaz 3 - الشارقة - United Arab Emirates',
        location: { lat: () => 25.2, lng: () => 55.27 },
      };
      const mockPlacePrediction = { toPlace: vi.fn(() => mockPlace) };
      const event = new Event('gmp-select') as Event & {
        placePrediction: typeof mockPlacePrediction;
      };
      event.placePrediction = mockPlacePrediction;

      await act(async () => {
        capturedHandler?.(event);
      });

      expect(mockPlacePrediction.toPlace).toHaveBeenCalledOnce();
      expect(mockPlace.fetchFields).toHaveBeenCalledWith({
        fields: ['displayName', 'formattedAddress', 'location'],
      });
      expect(onChange).toHaveBeenCalledWith(
        25.2,
        55.27,
        'Melting Moments Cakes, 21 Arab Club Street, Al Majaz 3, United Arab Emirates',
      );
      expect(mockPanTo).toHaveBeenCalledWith({ lat: 25.2, lng: 55.27 });
      expect(mockSetZoom).toHaveBeenCalledWith(15);
    });

    it('uses the approved shop address when Melting Moments Cakes is selected', async () => {
      let capturedHandler: ((e: Event) => void) | null = null;
      mockPlaceAutocompleteElement.addEventListener.mockImplementation(
        (_: string, handler: EventListenerOrEventListenerObject) => {
          capturedHandler = handler as (e: Event) => void;
        },
      );
      const { onChange } = renderLocationPicker();
      const mockPlace = {
        fetchFields: vi.fn().mockResolvedValue(undefined),
        displayName: 'Melting Moments Cakes',
        formattedAddress: '21 Arab Club Street - Al Majaz 3 - Sharjah - United Arab Emirates',
        location: { lat: () => 25.3304139, lng: () => 55.3736131 },
      };
      const event = new Event('gmp-select') as Event & {
        placePrediction: { toPlace: () => typeof mockPlace };
      };
      event.placePrediction = { toPlace: () => mockPlace };

      await act(async () => {
        capturedHandler?.(event);
      });

      expect(onChange).toHaveBeenCalledWith(
        25.3304139,
        55.3736131,
        'Melting Moments Cakes, Garden Tower 1 Shop no 1, Al Majaz 3, Sharjah',
      );
      expect(mockPanTo).toHaveBeenCalledWith({ lat: 25.3304139, lng: 55.3736131 });
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
      const event = new Event('gmp-select') as Event & {
        placePrediction: { toPlace: () => typeof mockPlace };
      };
      event.placePrediction = { toPlace: () => mockPlace };

      await act(async () => {
        capturedHandler?.(event);
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
        'gmp-select',
        expect.any(Function),
      );
    });
  });
});

// ─── Following the pin ────────────────────────────────────────────────────────

describe('LocationPicker viewport', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockBounds = null;
    // Without a key the component renders its "not configured" placeholder and
    // no map exists to pan.
    vi.stubEnv('NEXT_PUBLIC_GOOGLE_MAPS_API_KEY', 'test-api-key');
    mockPlaceAutocompleteElement = makeMockElement();
    mockUseMapsLibrary.mockReturnValue({
      // Called with `new`, so it has to be constructible.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      PlaceAutocompleteElement: vi.fn(function (this: any) {
        return mockPlaceAutocompleteElement;
      }),
    });
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('centres on the pin it is given, before the map reports any bounds', () => {
    // Opening a saved address for editing. The marker was always right; the
    // map used to stay wherever `defaultCenter` put it at mount, so the pin sat
    // off the edge of the view.
    renderLocationPicker({ lat: 25.33, lng: 55.37 });

    expect(mockPanTo).toHaveBeenCalledWith({ lat: 25.33, lng: 55.37 });
  });

  it('follows the pin when it moves somewhere off screen', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <LocationPicker lat={25.33} lng={55.37} onChange={onChange} />,
    );
    mockBounds = { contains: () => false };
    mockPanTo.mockClear();

    // Editing a different saved address without remounting the picker.
    rerender(<LocationPicker lat={24.47} lng={54.35} onChange={onChange} />);

    expect(mockPanTo).toHaveBeenCalledWith({ lat: 24.47, lng: 54.35 });
  });

  it('leaves the map alone when the pin is already in view', () => {
    // Tapping the map moves the pin. Re-centring on every tap would drag the
    // map out from under the finger that just tapped it.
    const onChange = vi.fn();
    mockBounds = { contains: () => true };
    const { rerender } = render(
      <LocationPicker lat={25.33} lng={55.37} onChange={onChange} />,
    );
    mockPanTo.mockClear();

    rerender(<LocationPicker lat={25.3301} lng={55.3701} onChange={onChange} />);

    expect(mockPanTo).not.toHaveBeenCalled();
  });

  it('does not try to centre on a pin that does not exist yet', () => {
    renderLocationPicker({ lat: null, lng: null });

    expect(mockPanTo).not.toHaveBeenCalled();
  });
});
