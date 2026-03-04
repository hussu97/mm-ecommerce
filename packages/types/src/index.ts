// Shared TypeScript types — expanded in Prompt 2+

export type Emirate =
  | "Dubai"
  | "Sharjah"
  | "Ajman"
  | "Abu Dhabi"
  | "Ras Al Khaimah"
  | "Fujairah"
  | "Umm Al Quwain";

export type OrderStatus = "created" | "confirmed" | "packed" | "cancelled";

export type DeliveryMethod = "delivery" | "pickup";

export type PaymentProvider = "stripe" | "tabby" | "tamara";

export type DiscountType = "percentage" | "fixed";

export interface ApiResponse<T> {
  data: T;
  message?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}
