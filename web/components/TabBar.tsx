"use client";
// Bottom tab bar, thumb reachable, active tab marked in the accent.
import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/", label: "Digest" },
  { href: "/alerts", label: "Alerts" },
  { href: "/company", label: "Company" },
  { href: "/research", label: "Research" },
];

export default function TabBar() {
  const path = usePathname();
  return (
    <nav className="fixed inset-x-0 bottom-0 z-10 border-t border-n-800 bg-ground/95 backdrop-blur pb-[env(safe-area-inset-bottom)]">
      <ul className="mx-auto grid max-w-[480px] grid-cols-4">
        {TABS.map((tab) => {
          const active = tab.href === "/" ? path === "/" : path.startsWith(tab.href);
          return (
            <li key={tab.href}>
              <Link
                href={tab.href}
                className={`block py-3 text-center text-xs font-medium tracking-wide uppercase ${
                  active ? "text-accent" : "text-n-400"
                }`}
              >
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
