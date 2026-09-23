"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";

export default function NewOrderPage() {
  return (
    <Suspense fallback={<div className="max-w-3xl mx-auto px-8 py-10 text-steel text-[14px]">Загрузка...</div>}>
      <NewOrderContent />
    </Suspense>
  );
}

function NewOrderContent() {
  const search = useSearchParams();
  const calcId = search.get("calc") || "";

  const [orderId, setOrderId] = useState<string>("");
  const [order, setOrder] = useState<any>(null);
  const [recs, setRecs] = useState<Record<string, any>>({});
  const [confirmed, setConfirmed] = useState(false);
  const user = "Imangali Armanuly"; // current manager (would come from auth in production)

  useEffect(() => {
    if (!calcId) return;
    api.recommendations(calcId).then((data) => {
      const map: Record<string, any> = {};
      data.items.forEach((i) => (map[i.sku] = i));
      setRecs(map);
    });
    api.draftOrder(calcId).then((res) => setOrderId(res.order_id));
  }, [calcId]);

  useEffect(() => {
    if (orderId) refreshOrder();
  }, [orderId]);

  async function refreshOrder() {
    const res = await api.exportOrder(orderId).catch(() => null); // not used for display, placeholder
    // fetch live order state
    const res2 = await fetch(`${process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000"}/api/orders/${orderId}`);
    setOrder(await res2.json());
  }

  async function changeQty(sku: string, value: number) {
    await api.adjustItem(orderId, sku, value, user);
    refreshOrder();
  }

  async function confirm() {
    await api.confirmOrder(orderId, calcId, user);
    setConfirmed(true);
    refreshOrder();
  }

  if (!order) {
    return <div className="max-w-3xl mx-auto px-8 py-10 text-steel text-[14px]">Формируем черновик заказа...</div>;
  }

  const items = Object.entries(order.items) as [string, any][];
  const totalUnits = items.reduce((s, [, d]) => s + d.quantity, 0);
  const manuallyAdjusted = items.filter(([, d]) => d.manually_adjusted);
  const suppliersInvolved = new Set(items.map(([sku]) => recs[sku]?.supplier).filter(Boolean));

  return (
    <div className="max-w-3xl mx-auto px-8 py-8">
      <h1 className="text-[20px] font-semibold text-ink mb-1">Подтверждение заказа</h1>
      <p className="text-[13px] text-steel mb-6">Черновик · {items.length} позиций</p>

      {!confirmed ? (
        <>
          <div className="bg-white border border-line rounded-md overflow-hidden mb-6">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-line text-left text-steel">
                  <th className="px-4 py-2 font-medium">Артикул</th>
                  <th className="px-4 py-2 font-medium">Наименование</th>
                  <th className="px-4 py-2 font-medium">Поставщик</th>
                  <th className="px-4 py-2 font-medium text-right">Рекомендовано</th>
                  <th className="px-4 py-2 font-medium text-right">Количество</th>
                </tr>
              </thead>
              <tbody>
                {items.map(([sku, d]) => (
                  <tr key={sku} className="border-b border-line last:border-0">
                    <td className="px-4 py-2 font-mono text-[12px]">{sku}</td>
                    <td className="px-4 py-2">{recs[sku]?.name || sku}</td>
                    <td className="px-4 py-2 text-steel">{recs[sku]?.supplier}</td>
                    <td className="px-4 py-2 text-right text-steel">{d.recommended}</td>
                    <td className="px-4 py-2 text-right">
                      <input
                        type="number"
                        defaultValue={d.quantity}
                        onBlur={(e) => changeQty(sku, Number(e.target.value))}
                        className={`w-24 text-right border rounded px-2 py-1 ${
                          d.manually_adjusted ? "border-copper bg-copper/5" : "border-line"
                        }`}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="bg-white border border-line rounded-md p-5 mb-6">
            <h2 className="text-[14px] font-semibold mb-3">Итог перед подтверждением</h2>
            <div className="grid grid-cols-3 gap-4 text-[13px]">
              <div><span className="text-steel">Поставщиков:</span> {suppliersInvolved.size}</div>
              <div><span className="text-steel">Позиций:</span> {items.length}</div>
              <div><span className="text-steel">Всего единиц:</span> {totalUnits}</div>
            </div>
            {manuallyAdjusted.length > 0 && (
              <div className="mt-2 text-[13px] text-copper">
                Изменено вручную: {manuallyAdjusted.length} позиций
              </div>
            )}
          </div>

          <button
            onClick={confirm}
            className="bg-accent text-white text-[14px] font-medium px-5 py-2.5 rounded-md hover:opacity-90"
          >
            Подтвердить заказ
          </button>
        </>
      ) : (
        <ConfirmedView orderId={orderId} />
      )}
    </div>
  );
}

function ConfirmedView({ orderId }: { orderId: string }) {
  const [csv, setCsv] = useState<string>("");
  useEffect(() => {
    api.exportOrder(orderId).then((r) => setCsv(r.content));
  }, [orderId]);

  return (
    <div className="bg-white border border-line rounded-md p-6">
      <div className="text-[15px] font-semibold text-ok mb-2">Заказ подтверждён</div>
      <p className="text-[13px] text-steel mb-4">
        Автоматическая отправка поставщику не выполняется — заказ готов к экспорту или передаче в учётную систему вручную.
      </p>
      <a
        href={`data:text/csv;charset=utf-8,${encodeURIComponent(csv)}`}
        download={`order-${orderId}.csv`}
        className="inline-block bg-ink text-white text-[13px] px-4 py-2 rounded-md"
      >
        Экспортировать (CSV)
      </a>
    </div>
  );
}
