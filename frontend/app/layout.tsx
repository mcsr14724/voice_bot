import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vox — Voice workspace",
  description: "A private real-time voice conversation workspace.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
