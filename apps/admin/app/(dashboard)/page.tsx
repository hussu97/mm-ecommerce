'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ordersApi, productsApi, promoApi } from '@/lib/api';
import type { Order, PromoCode } from '@/lib/types';
import { Badge } from '@/components/ui';
import { formatCurrency, formatDate } from '@/lib/utils';

const STATUS_BADGE: Record<string, 'warning' | 'info' | 'success' | 'danger'> = {
  created:   'warning',
  confirmed: 'info',
  packed:    'success',
  cancelled: 'danger',
};

export default function DashboardPage() {
  const [recentOrders, setRecentOrders] = useState<Order[]>([]);
  const [totalProducts, setTotalProducts] = useState<number | null>(null);
  const [activePromos, setActivePromos] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      ordersApi.listAll({ per_page: 10 }),
      productsApi.list({ per_page: 1 }),
      promoApi.list(),
    ]).then(([orders, products, promos]) => {
      setRecentOrders(orders.items);
      setTotalProducts(products.total);
      setActivePromos((promos as PromoCode[]).filter(p => p.is_active).length);
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  // Compute today's stats from recent orders
  const today = new Date().toDateString();
  const todayOrders = recentOrders.filter(o => new Date(o.created_at).toDateString() === today);
  const todayRevenue = todayOrders.reduce((sum, o) => sum + Number(o.total), 0);

  const METRICS = [
    {
      label: "Today's Orders",
      value: loading ? '—' : String(todayOrders.length),
      icon: 'receipt_long',
      href: '/orders',
    },
    {
      label: "Today's Revenue",
      value: loading ? '—' : formatCurrency(todayRevenue),
      icon: 'payments',
      href: '/orders',
    },
    {
      label: 'Total Products',
      value: loading ? '—' : String(totalProducts ?? 0),
      icon: 'inventory_2',
      href: '/products',
    },
    {
      label: 'Active Promos',
      value: loading ? '—' : String(activePromos ?? 0),
      icon: 'local_offer',
      href: '/promo-codes',
    },
  ];

  return (
    <div>
      <div className="mb-6">
        <h1 className="font-display text-2xl text-gray-800">Dashboard</h1>
        <p className="text-xs text-gray-400 font-body mt-0.5">
          {new Date().toLocaleDateString('en-AE', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}
        </p>
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        {METRICS.map(({ label, value, icon, href }) => (
          <Link
            key={label}
            href={href}
            className="bg-white border border-gray-200 p-4 hover:border-primary transition-colors group"
          >
            <div className="flex items-start justify-between mb-3">
              <span className="material-icons text-secondary text-xl group-hover:text-primary transition-colors">{icon}</span>
            </div>
            <div className="font-display text-2xl text-gray-800 mb-1">{value}</div>
            <div className="text-[11px] font-body uppercase tracking-widest text-gray-400">{label}</div>
          </Link>
        ))}
      </div>

      {/* Quick Actions */}
      <div className="mb-8">
        <h2 className="text-xs font-body uppercase tracking-widest text-gray-500 mb-3">Quick Actions</h2>
        <div className="flex flex-wrap gap-2">
          <Link href="/products/new" className="flex items-center gap-1.5 px-4 py-2 bg-primary text-white text-xs font-body uppercase tracking-widest hover:opacity-90 transition-opacity">
            <span className="material-icons text-[14px]">add</span>
            New Product
          </Link>
          <Link href="/categories" className="flex items-center gap-1.5 px-4 py-2 border border-gray-300 text-gray-600 text-xs font-body uppercase tracking-widest hover:bg-gray-50 transition-colors">
            <span className="material-icons text-[14px]">add</span>
            New Category
          </Link>
          <Link href="/orders" className="flex items-center gap-1.5 px-4 py-2 border border-gray-300 text-gray-600 text-xs font-body uppercase tracking-widest hover:bg-gray-50 transition-colors">
            <span className="material-icons text-[14px]">visibility</span>
            View All Orders
          </Link>
        </div>
      </div>

      {/* Recent Orders */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xs font-body uppercase tracking-widest text-gray-500">Recent Orders</h2>
          <Link href="/orders" className="text-xs text-primary hover:underline font-body">View all</Link>
        </div>

        {loading ? (
          <div className="space-y-2">
            {[1,2,3].map(i => <div key={i} className="h-12 bg-gray-100 animate-pulse" />)}
          </div>
        ) : recentOrders.length === 0 ? (
          <div className="text-center py-10 bg-white border border-gray-200">
            <p className="text-sm text-gray-400 font-body">No orders yet</p>
          </div>
        ) : (
          <div className="bg-white border border-gray-200 divide-y divide-gray-100">
            {recentOrders.map(order => (
              <Link
                key={order.id}
                href={`/orders`}
                className="flex items-center justify-between px-4 py-3 hover:bg-gray-50 transition-colors"
              >
                <div className="flex items-center gap-4 min-w-0">
                  <span className="text-xs font-mono text-gray-700 font-medium shrink-0">{order.order_number}</span>
                  <span className="text-xs text-gray-400 font-body truncate hidden sm:block">{order.email}</span>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <Badge variant={STATUS_BADGE[order.status] ?? 'neutral'}>{order.status}</Badge>
                  <span className="text-xs font-body text-gray-700">{formatCurrency(order.total)}</span>
                  <span className="text-[11px] text-gray-400 font-body hidden sm:block">{formatDate(order.created_at)}</span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
