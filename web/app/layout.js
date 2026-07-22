import "./globals.css";
import { Inter, Space_Grotesk, Space_Mono } from "next/font/google";

// PLAN §8.1: Space Grotesk 700 for display, Inter 400 for body, Space Mono for
// every number (tabular). Self-hosted by next/font at build time — no runtime
// request to Google, so the board still renders if that CDN is unreachable.
const grotesk = Space_Grotesk({
  subsets: ["latin"], weight: ["500", "700"], variable: "--font-grotesk", display: "swap",
});
const inter = Inter({
  subsets: ["latin"], variable: "--font-inter", display: "swap",
});
const mono = Space_Mono({
  subsets: ["latin"], weight: ["400", "700"], variable: "--font-mono", display: "swap",
});

export const metadata = { title: "SEPA Board", description: "Minervini screener — Bursa Malaysia" };

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={`${grotesk.variable} ${inter.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
