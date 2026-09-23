"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams, useParams } from "next/navigation";
import { LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer } from "recharts";
import { api } from "@/lib/api";
import UrgencyBadge from "@/components/UrgencyBadge";

export default function ProductDetailPage() {
  return (
    <Suspense fallback={<div className="max-w-4xl mx-auto px-8 py-10 text-steel text-[14px]">Загрузка...</div>}>
      <ProductDetailContent />
    </Suspense>
  );
}

function ProductDetailContent() {
  const { sku } = useParams<{ sku: string }>();
  const search = useSearchParams();
  const calcId = search.get("calc") || "";

  const [detail, setDetail] = useState<any>(null);
  const [explanation, setExplanation] = useState<string>("");
  const [explaining, setExplaining] = useState(false);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [asking, setAsking] = useState(false);
  const [showFactors, setShowFactors] = useState(false);

  useEffect(() => {
    if (calcId && sku) {
      api.productDetail(calcId, sku as string).then(setDetail).catch(() => {});
    }
  }, [calcId, sku]);

  async function getExplanation() {
    setExplaining(true);
    try {
      const res = await api.explain(calcId, sku as string);
      setExplanation(res.explanation);
    } finally {
      setExplaining(false);
    }
  }

  async function askQuestion() {
    if (!question.trim()) return;
    setAsking(true);
    try {
      const res = await api.ask(calcId, sku as string, question);
      setAnswer(res.answer);
    } finally {
      setAsking(false);
    }
  }

  if (!detail) {
    return <div className="max-w-4xl mx-auto px-8 py-10 text-steel text-[14px]">Загрузка...</div>;
  }

  return (
    <div className="max-w-4xl mx-auto px-8 py-8">
      <div className="mb-6">
        <div className="text-[13px] text-steel font-mono">{detail.sku}</div>
        <h1 className="text-[20px] font-semibold text-ink">{detail.name}</h1>
        <div className="text-[13px] text-steel mt-0.5">{detail.category} · {detail.supplier}</div>
      </div>

      <div className="grid grid-cols-4 gap-4 mb-6">
        <Stat label="Остаток" value={detail.current_stock} />
        <Stat label="В пути" value={detail.in_transit} />
        <Stat label="Прогноз спроса" value={detail.forecast_demand} />
        <div className="bg-white border border-line rounded-md px-4 py-3">
          <div className="text-[12px] text-steel mb-1">Рекомендуется заказать</div>
          <div className="text-[22px] font-semibold">{detail.recommended_quantity}</div>
        </div>
      </div>

      <div className="mb-6">
        <UrgencyBadge urgency={detail.urgency} />
      </div>

      {/* Sales history chart */}
      <Section title="История продаж и прогноз">
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={detail.monthly_sales}>
            <CartesianGrid stroke="#EEF1F4" />
            <XAxis dataKey="month" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Line type="monotone" dataKey="units" stroke="#2C5F73" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </Section>

      {/* Explanation */}
      <Section title="Объяснение">
        <p className="text-[14px] text-ink leading-relaxed mb-3">
          {detail.reasons.join(" ")}
        </p>
        {!explanation && (
          <button
            onClick={getExplanation}
            disabled={explaining}
            className="text-[13px] text-accent font-medium hover:underline disabled:opacity-50"
          >
            {explaining ? "Формируем пояснение..." : "Пояснить простыми словами →"}
          </button>
        )}
        {explanation && (
          <div className="bg-canvas border border-line rounded-md p-4 text-[14px] leading-relaxed">
            {explanation}
          </div>
        )}
      </Section>

      {/* Ask a question */}
      <Section title="Вопрос по расчёту">
        <div className="flex gap-2 mb-3">
          <input
            className="input flex-1"
            placeholder="Например: почему не учли акцию в марте?"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <button
            onClick={askQuestion}
            disabled={asking}
            className="bg-ink text-white text-[13px] px-4 py-2 rounded-md disabled:opacity-50"
          >
            {asking ? "..." : "Спросить"}
          </button>
        </div>
        {answer && <div className="bg-canvas border border-line rounded-md p-4 text-[14px]">{answer}</div>}
      </Section>

      {/* Factors */}
      <button
        onClick={() => setShowFactors((s) => !s)}
        className="text-[13px] text-steel hover:text-ink mb-2"
      >
        {showFactors ? "Скрыть факторы расчёта" : "Подробнее — факторы расчёта"}
      </button>
      {showFactors && (
        <pre className="bg-graphite text-white text-[12px] rounded-md p-4 overflow-auto">
          {JSON.stringify(detail.debug, null, 2)}
        </pre>
      )}

      <style jsx global>{`
        .input { border: 1px solid #dde2e8; border-radius: 6px; padding: 7px 10px; font-size: 14px; }
      `}</style>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="bg-white border border-line rounded-md px-4 py-3">
      <div className="text-[12px] text-steel mb-1">{label}</div>
      <div className="text-[18px] font-semibold">{value}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-8">
      <h2 className="text-[14px] font-semibold text-ink mb-3">{title}</h2>
      {children}
    </div>
  );
}
