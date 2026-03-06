'use client';

import { useState } from 'react';
import Image from 'next/image';

interface ProductImageGalleryProps {
  images: string[];
  name: string;
}

export function ProductImageGallery({ images, name }: ProductImageGalleryProps) {
  const [mainImage, setMainImage] = useState(images[0] ?? null);

  return (
    <div className="flex flex-col gap-3">
      <div className="relative aspect-square overflow-hidden bg-[#f9f5f0] group">
        {mainImage ? (
          <Image
            src={mainImage}
            alt={name}
            fill
            sizes="(max-width: 1024px) 100vw, 50vw"
            className="object-cover group-hover:scale-110 transition-transform duration-300"
            priority
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <span className="material-icons text-8xl text-secondary">cake</span>
          </div>
        )}
      </div>

      {/* Thumbnails */}
      {images.length > 1 && (
        <div className="flex gap-2 overflow-x-auto">
          {images.map((url, i) => (
            <button
              key={i}
              onClick={() => setMainImage(url)}
              className={[
                'relative w-20 h-20 shrink-0 overflow-hidden bg-[#f9f5f0] transition-all',
                mainImage === url
                  ? 'ring-2 ring-primary'
                  : 'opacity-60 hover:opacity-100',
              ].join(' ')}
              aria-label={`View image ${i + 1}`}
              aria-current={mainImage === url ? 'true' : undefined}
            >
              <Image
                src={url}
                alt={`${name} image ${i + 1}`}
                fill
                sizes="80px"
                className="object-cover"
              />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
