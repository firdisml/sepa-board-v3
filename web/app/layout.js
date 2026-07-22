import "./globals.css";

export const metadata = { title: "SEPA Board", description: "Minervini screener — Bursa Malaysia" };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
