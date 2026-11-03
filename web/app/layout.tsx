// Root shell: dark ground, single column sized for a phone, bottom tab bar.
import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import TabBar from "@/components/TabBar";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export const metadata: Metadata = {
  title: "FINN",
  description: "Filings triage, promise ledger and alerts for Indian listed companies",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#0b0c0e",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body>
        <main className="mx-auto max-w-[480px] px-4 pt-[calc(env(safe-area-inset-top)+12px)] pb-[calc(env(safe-area-inset-bottom)+72px)]">
          {children}
        </main>
        <TabBar />
      </body>
    </html>
  );
}
