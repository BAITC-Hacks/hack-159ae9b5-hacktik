"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function SuppliersPage() {
  const [suppliers, setSuppliers] = useState<string[]>([]);
  useEffect(() => {
    api.suppliers().then(setSuppliers).catch(() => {});
  }, []);

  return (
    <div className="max-w-4xl mx-auto px-8 py-8">
      <h1 className="text-[20px] font-semibold text-ink mb-6">Поставщики</h1>
      <div className="bg-white border border-line rounded-md divide-y divide-line">
        {suppliers.map((s) => (
          <div key={s} className="px-4 py-3 text-[14px]">{s}</div>
        ))}
        {suppliers.length === 0 && (
          <div className="px-4 py-6 text-[13px] text-steel">Нет данных о поставщиках.</div>
        )}
      </div>
    </div>
  );
}
