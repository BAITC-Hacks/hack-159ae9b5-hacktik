"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api, CalculateResponse, RecommendationItem } from "@/lib/api";
import UrgencyBadge from "@/components/UrgencyBadge";

type ViewState = "idle" | "loading" | "ready" | "empty" | "error";
type GroupMode = "flat" | "supplier";

export default function DashboardPage() {
  const [warehouses, setWarehouses] = useState<string[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [warehouse, setWarehouse] = useState<string>("");
  const [category, setCategory] = useState<string>("");
  const [state, setState] = useState<ViewState>("idle");
  const [data, setData] = useState<CalculateResponse | null>(null);
  const [group, setGroup] = useState<GroupMode>("supplier");
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    api.warehouses().then(setWarehouses).catch(() => {});
    api.categories().then(setCategories).catch(() => {});
  }, []);

  async function runCalculation() {
    setState("loading");
    setErrorMsg("");
    try {
      const res = await api.calculate({
        warehouse: warehouse || undefined,
        category: category || undefined,
      });
      setData(res);
      setState(res.items.length === 0 ? "empty" : "ready");
    } catch (e: any) {
      setErrorMsg(e.message || "Не удалось выполнить расчёт");
      setState("error");
    }
  }

  const grouped: Record<string, RecommendationItem[]> = {};
  if (data) {
    for (const item of data.items) {
      (grouped[item.supplier] ||= []).push(item);
    }
  }

  return (
    <div className="max-w-6xl mx-auto px-8 py-8">
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="text-[22px] font-semibold text-ink">Заказы на закупку</h1>
      </div>

      {/* Filters */}
      <div className="bg-white border border-line rounded-md p-4 flex flex-wrap items-end gap-4 mb-6">
        <Field label="Склад">
          <select className="input" value={warehouse} onChange={(e) => setWarehouse(e.target.value)}>
            <option value="">Все склады</option>
            {warehouses.map((w) => (
              <option key={w} value={w}>{w}</option>
            ))}
          </select>
        </Field>
        <Field label="Категория">
          <select className="input" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">Все категории</option>
            {categories.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </Field>
        <button
          onClick={runCalculation}
          disabled={state === "loading"}
          className="ml-auto bg-accent text-white text-[14px] font-medium px-5 py-2.5 rounded-md hover:opacity-90 disabled:opacity-50"
        >
          {state === "loading" ? "Рассчитываем..." : "Рассчитать потребность"}
        </button>
      </div>

      {/* States */}
      {state === "idle" && (
        <EmptyState text="Выберите параметры и запустите расчёт." />
      )}
      {state === "loading" && <EmptyState text="Рассчитываем потребность..." />}
      {state === "error" && (
        <EmptyState text={`Ошибка при расчёте. ${errorMsg || "Попробуйте ещё раз."}`} tone="error" />
      )}
      {state === "empty" && (
        <EmptyState text="Для выбранных параметров рекомендации не найдены." />
      )}

      {state === "ready" && data && (
        <>
          {/* Summary */}
          <div className="grid grid-cols-4 gap-4 mb-6">
            <SummaryCard label="Позиций к закупке" value={data.summary.items_count} />
            <SummaryCard label="Срочных позиций" value={data.summary.urgent_count} accent="#B4472E" />
            <SummaryCard label="Поставщиков" value={data.summary.suppliers_count} />
            <SummaryCard label="Общий объём" value={`${data.summary.total_units} шт.`} />
          </div>

          <div className="flex items-center justify-between mb-3">
            <div className="flex gap-1 text-[13px]">
              <ToggleBtn active={group === "supplier"} onClick={() => setGroup("supplier")}>
                По поставщикам
              </ToggleBtn>
              <ToggleBtn active={group === "flat"} onClick={() => setGroup("flat")}>
                Общий список
              </ToggleBtn>
            </div>
            <Link
              href={`/orders/new?calc=${data.calculation_id}`}
              className="text-[13px] text-accent font-medium hover:underline"
            >
              Перейти к подтверждению заказа →
            </Link>
          </div>

          {group === "flat" ? (
            <RecTable items={data.items} calcId={data.calculation_id} />
          ) : (
            Object.entries(grouped).map(([supplier, items]) => (
              <div key={supplier} className="mb-6">
                <div className="text-[13px] font-semibold text-steel mb-2">
                  Поставщик: {supplier} · {items.length} поз. ·{" "}
                  {items.reduce((s, i) => s + i.recommended_quantity, 0)} шт.
                </div>
                <RecTable items={items} calcId={data.calculation_id} />
              </div>
            ))
          )}
        </>
      )}

      <style jsx global>{`
        .input {
          border: 1px solid #dde2e8;
          border-radius: 6px;
          padding: 7px 10px;
          font-size: 14px;
          background: white;
          min-width: 200px;
        }
      `}</style>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-[12px] text-steel">{label}</label>
      {children}
    </div>
  );
}

function SummaryCard({ label, value, accent }: { label: string; value: string | number; accent?: string }) {
  return (
    <div className="bg-white border border-line rounded-md px-4 py-3">
      <div className="text-[12px] text-steel mb-1">{label}</div>
      <div className="text-[22px] font-semibold" style={{ color: accent || "#161B22" }}>{value}</div>
    </div>
  );
}

function ToggleBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 rounded-md border ${
        active ? "bg-ink text-white border-ink" : "bg-white text-steel border-line"
      }`}
    >
      {children}
    </button>
  );
}

function EmptyState({ text, tone }: { text: string; tone?: "error" }) {
  return (
    <div className={`border rounded-md px-6 py-10 text-center text-[14px] ${
      tone === "error" ? "border-urgent/30 bg-urgent/5 text-urgent" : "border-line bg-white text-steel"
    }`}>
      {text}
    </div>
  );
}

function RecTable({ items, calcId }: { items: RecommendationItem[]; calcId: string }) {
  return (
    <div className="bg-white border border-line rounded-md overflow-hidden">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-line text-left text-steel">
            <Th>Артикул</Th>
            <Th>Наименование</Th>
            <Th>Категория</Th>
            <Th align="right">Остаток</Th>
            <Th align="right">В пути</Th>
            <Th align="right">Прогноз</Th>
            <Th align="right">Заказать</Th>
            <Th>Срочность</Th>
            <Th>Обоснование</Th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.sku} className="border-b border-line last:border-0 hover:bg-canvas">
              <td className="px-4 py-2.5 font-mono text-[12px]">
                <Link href={`/products/${item.sku}?calc=${calcId}`} className="text-accent hover:underline">
                  {item.sku}
                </Link>
              </td>
              <td className="px-4 py-2.5">{item.name}</td>
              <td className="px-4 py-2.5 text-steel">{item.category}</td>
              <td className="px-4 py-2.5 text-right">{item.current_stock}</td>
              <td className="px-4 py-2.5 text-right">{item.in_transit}</td>
              <td className="px-4 py-2.5 text-right">{item.forecast_demand}</td>
              <td className="px-4 py-2.5 text-right font-semibold">{item.recommended_quantity}</td>
              <td className="px-4 py-2.5"><UrgencyBadge urgency={item.urgency} /></td>
              <td className="px-4 py-2.5 text-steel max-w-xs truncate" title={item.reasons.join("; ")}>
                {item.reasons[0]}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Th({ children, align }: { children: React.ReactNode; align?: "right" }) {
  return (
    <th className={`px-4 py-2 text-[12px] font-medium ${align === "right" ? "text-right" : "text-left"}`}>
      {children}
    </th>
  );
}
