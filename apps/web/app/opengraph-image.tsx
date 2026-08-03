import { ImageResponse } from 'next/og';

/**
 * The share card.
 *
 * What used to be shared was the 800×800 logo. Both Twitter's
 * `summary_large_image` and Facebook want 1200×630, so a square went out
 * cropped down the sides or was dropped for being the wrong ratio — on every
 * link anyone posted.
 *
 * This is drawn rather than photographed on purpose: it stays correct when the
 * menu changes, and it carries the two things a share card has to answer at a
 * glance — who this is, and whether they deliver where you are.
 */
export const alt =
  'Melting Moments Cakes — brownies, cookies and desserts delivered across the UAE';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

const PLUM = '#8a5a64';
const CREAM = '#f9f5f0';

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: CREAM,
          color: PLUM,
          position: 'relative',
        }}
      >
        {/* Frame — the same thin rule the site uses under its section headings */}
        <div
          style={{
            position: 'absolute',
            top: 36,
            left: 36,
            right: 36,
            bottom: 36,
            border: `2px solid ${PLUM}`,
            opacity: 0.35,
          }}
        />

        <div
          style={{
            fontSize: 22,
            letterSpacing: 10,
            textTransform: 'uppercase',
            opacity: 0.75,
            marginBottom: 28,
          }}
        >
          Baked in Sharjah
        </div>

        <div style={{ fontSize: 92, lineHeight: 1.05, textAlign: 'center' }}>
          Melting Moments
        </div>
        <div style={{ fontSize: 92, lineHeight: 1.05, textAlign: 'center' }}>
          Cakes
        </div>

        <div
          style={{
            width: 220,
            height: 2,
            background: PLUM,
            opacity: 0.4,
            margin: '38px 0',
          }}
        />

        <div
          style={{
            fontSize: 32,
            textAlign: 'center',
            maxWidth: 860,
            lineHeight: 1.4,
          }}
        >
          Brownies, cookies and desserts delivered across the UAE
        </div>

        <div
          style={{
            marginTop: 26,
            fontSize: 22,
            letterSpacing: 3,
            textTransform: 'uppercase',
            opacity: 0.7,
          }}
        >
          Dubai · Sharjah · Ajman · Abu Dhabi
        </div>
      </div>
    ),
    size,
  );
}
