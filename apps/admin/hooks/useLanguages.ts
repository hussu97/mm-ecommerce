import { useEffect, useState } from 'react';
import { languagesApi } from '@/lib/api';
import type { Language } from '@/lib/types';

export function useLanguages() {
  const [languages, setLanguages] = useState<Language[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    languagesApi.listAll()
      .then(all => setLanguages(all.filter(l => l.is_active && !l.is_default)))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return { languages, loading };
}
