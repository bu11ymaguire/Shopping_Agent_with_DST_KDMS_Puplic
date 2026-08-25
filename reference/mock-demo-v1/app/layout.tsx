import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RA-Rec + SPN Hybrid Shopping Demo",
  description: "Hybrid conversational shopping demo combining RA-Rec and SPN agent workflows"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
