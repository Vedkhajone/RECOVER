import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "RECOVER — AI revenue recovery",
  description:
    "AI that finds slipping revenue, recovers what it can, and knows when to stop. " +
    "A prototype built for the Razorpay Buildathon using Razorpay Test Mode.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
