export default function HistoryPage() {
  return (
    <div className="max-w-4xl mx-auto px-8 py-8">
      <h1 className="text-[20px] font-semibold text-ink mb-6">История расчётов</h1>
      <div className="bg-white border border-line rounded-md px-6 py-10 text-center text-[14px] text-steel">
        Здесь появится список ранее выполненных расчётов после подключения хранилища
        (сейчас расчёты хранятся в памяти сервера на время сессии).
      </div>
    </div>
  );
}
