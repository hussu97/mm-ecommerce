'use client';

import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { authApi } from './api';
import { User } from './types';

interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (data: { email: string; password: string; phone?: string }) => Promise<void>;
  logout: () => Promise<void>;
  setUser: (user: User) => void;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    authApi.me()
      .then((u) => {
        // Don't treat guest users as logged-in — they exist only for checkout.
        if (!u.is_guest) setUser(u);
      })
      .catch(() => {/* no-op — no valid session */})
      .finally(() => setIsLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await authApi.login(email, password);
    setUser(res.user);
  }, []);

  const register = useCallback(async (data: { email: string; password: string; phone?: string }) => {
    const res = await authApi.register(data);
    setUser(res.user);
  }, []);

  /**
   * Logs out the current user.
   * Calls the backend to revoke the refresh token and clear httpOnly cookies.
   */
  const logout = useCallback(async () => {
    await authApi.logout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, isLoading, login, register, logout, setUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
