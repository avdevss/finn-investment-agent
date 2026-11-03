// Company index: every tracked company with its last close, a way into each company page.
import Link from "next/link";
import { getCompanyList } from "@/lib/company";
import { num, signedPct } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function CompanyIndex() {
  const companies = await getCompanyList();
  const groups = [
    { title: "Portfolio", items: companies.filter((c) => c.in_portfolio) },
    { title: "Research", items: companies.filter((c) => !c.in_portfolio) },
  ];
  return (
    <>
      <h1 className="mb-2 text-base font-semibold text-n-50">Companies</h1>
      {groups.filter((g) => g.items.length > 0).map((g) => (
        <section key={g.title} className="mb-4">
          <h2 className="num mb-1 text-xs uppercase tracking-wide text-n-500">{g.title} · {g.items.length}</h2>
          <ul>
            {g.items.map((c) => (
              <li key={c.symbol} className="border-b border-n-800">
                <Link href={`/company/${c.symbol}`} className="grid grid-cols-[1fr_auto_auto] items-baseline gap-3 py-2">
                  <span className="min-w-0 truncate">
                    <span className="font-semibold text-n-50">{c.symbol}</span>{" "}
                    <span className="text-xs text-n-500">{c.name}</span>
                  </span>
                  <span className="num text-right text-n-50">{c.close === null ? "—" : `₹${num(c.close)}`}</span>
                  <span className="num w-14 text-right text-xs text-n-400">{signedPct(c.pct_change, 2)}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}
