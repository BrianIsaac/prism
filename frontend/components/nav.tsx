"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// Hoisted tab list — ref-stable across renders. Admin is grouped with the two
// primary tabs so the surface area stays simple.
const TABS: ReadonlyArray<{ href: string; label: string }> = [
  { href: "/", label: "Explore" },
  { href: "/new", label: "New Route" },
  { href: "/admin", label: "Admin" },
];

export function Nav() {
  const pathname = usePathname() ?? "/";

  return (
    <nav className="h-12 flex items-center gap-6 px-6 border-b border-white/10 bg-black/60 backdrop-blur shrink-0">
      <Link
        href="/"
        className="text-sm font-semibold tracking-tight text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60 rounded"
      >
        Prism
      </Link>
      <div className="h-6 w-px bg-white/10" aria-hidden="true" />
      <ul className="flex gap-1 items-center">
        {TABS.map((tab) => {
          const active =
            pathname === tab.href ||
            (tab.href !== "/" && pathname.startsWith(tab.href));
          return (
            <li key={tab.href}>
              <Link
                href={tab.href}
                aria-current={active ? "page" : undefined}
                className={`px-3 py-1 rounded text-xs uppercase tracking-wider transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-grab-green/60 ${
                  active
                    ? "text-grab-green bg-grab-green/10"
                    : "text-white/50 hover:text-white hover:bg-white/5"
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
