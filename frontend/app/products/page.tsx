import Link from "next/link";

export default function ProductsPage() {
  return (
    <div className="max-w-4xl mx-auto px-8 py-8">
      <h1 className="text-[20px] font-semibold text-ink mb-6">Товары</h1>
      <div className="bg-white border border-line rounded-md px-6 py-10 text-center text-[14px] text-steel">
        Откройте карточку товара из таблицы рекомендаций после расчёта, чтобы увидеть детали.{" "}
        <Link href="/" className="text-accent hover:underline">Перейти к расчёту →</Link>
      </div>
    </div>
  );
}
