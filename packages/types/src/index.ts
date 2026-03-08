// Shared TypeScript types — expanded in Prompt 2+

export type RegionCode =
  | "dubai"
  | "sharjah"
  | "ajman"
  | "abu_dhabi"
  | "fujairah"
  | "ras_al_khaimah"
  | "umm_al_quwain"
  | "al_ain"
  | "rest_of_uae";

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
