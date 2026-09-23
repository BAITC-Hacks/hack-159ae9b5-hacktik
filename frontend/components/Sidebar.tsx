"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Заказы на закупку" },
  { href: "/suppliers", label: "Поставщики" },
  { href: "/products", label: "Товары" },
  { href: "/history", label: "История расчётов" },
];

export default function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="w-60 shrink-0 border-r border-line bg-ink text-canvas flex flex-col">
      <div className="px-5 py-5 border-b border-white/10">
        <div className="text-sm tracking-wide text-white/50">ИЭК</div>
        <div className="text-[15px] font-semibold">Электрокомплект</div>
      </div>
      <nav className="flex-1 py-3">
        {NAV.map((item) => {
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`block px-5 py-2.5 text-[14px] border-l-2 transition-colors ${
                active
                  ? "border-copper bg-white/5 text-white font-medium"
                  : "border-transparent text-white/70 hover:text-white hover:bg-white/5"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="px-5 py-4 border-t border-white/10 text-[13px] text-white/50">
        Менеджер по закупкам
      </div>
    </aside>
  );
}
