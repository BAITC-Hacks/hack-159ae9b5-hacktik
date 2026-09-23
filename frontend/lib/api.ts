const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export type Urgency = "low" | "medium" | "high";

export interface RecommendationItem {
  sku: string;
  name: string;
  category: string;
  supplier: string;
  current_stock: number;
  in_transit: number;
  forecast_demand: number;
  recommended_quantity: number;
  urgency: Urgency;
  reasons: string[];
  moq?: number;
}

export interface CalculateResponse {
  calculation_id: string;
  warehouse?: string;
  category?: string;
  generated_at: string;
  summary: {
    items_count: number;
    urgent_count: number;
    suppliers_count: number;
    total_units: number;
  };
  items: RecommendationItem[];
}

async function req<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
  });
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
  return res.json();
}

export const api = {
  warehouses: () => req<string[]>("/api/warehouses"),
  categories: () => req<string[]>("/api/categories"),
  suppliers: () => req<string[]>("/api/suppliers"),
  calculate: (body: { warehouse?: string; category?: string }) =>
    req<CalculateResponse>("/api/calculate", { method: "POST", body: JSON.stringify(body) }),
  recommendations: (calcId: string) =>
    req<CalculateResponse>(`/api/recommendations/${calcId}`),
  productDetail: (calcId: string, sku: string) =>
    req(`/api/recommendations/${calcId}/product/${sku}`),
  draftOrder: (calcId: string) =>
    req<{ order_id: string; status: string }>("/api/orders/draft", {
      method: "POST",
      body: JSON.stringify({ calculation_id: calcId }),
    }),
  adjustItem: (orderId: string, sku: string, newQuantity: number, user: string) =>
    req(`/api/orders/${orderId}/items/${sku}`, {
      method: "PATCH",
      body: JSON.stringify({ new_quantity: newQuantity, user }),
    }),
  confirmOrder: (orderId: string, calcId: string, user: string) =>
    req(`/api/orders/${orderId}/confirm`, {
      method: "POST",
      body: JSON.stringify({ calculation_id: calcId, user }),
    }),
  exportOrder: (orderId: string) => req<{ format: string; content: string }>(`/api/orders/${orderId}/export`),
  explain: (calcId: string, sku: string) =>
    req<{ explanation: string }>("/api/ai/explain", {
      method: "POST",
      body: JSON.stringify({ calculation_id: calcId, sku }),
    }),
  ask: (calcId: string, sku: string, question: string) =>
    req<{ answer: string }>("/api/ai/ask", {
      method: "POST",
      body: JSON.stringify({ calculation_id: calcId, sku, question }),
    }),
};

export const URGENCY_LABEL: Record<Urgency, string> = {
  high: "Высокая",
  medium: "Средняя",
  low: "Низкая",
};

export const URGENCY_COLOR: Record<Urgency, string> = {
  high: "#B4472E",
  medium: "#B4842E",
  low: "#2E7D5B",
};
