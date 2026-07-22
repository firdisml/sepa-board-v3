import "./globals.css";

export const metadata = { title: "SEPA Board", description: "Minervini screener — US market" };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
