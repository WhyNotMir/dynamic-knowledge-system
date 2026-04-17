import { Cormorant_Garamond, DM_Sans, JetBrains_Mono } from "next/font/google";
import { Toaster } from "sonner";
import Providers from "@/components/providers";
import "./globals.css";

const cormorant = Cormorant_Garamond({
  subsets: ["latin"],
  variable: "--font-cormorant",
  weight: ["400", "500", "600", "700"],
});

const dmSans = DM_Sans({
  subsets: ["latin"],
  variable: "--font-dm-sans",
});

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
  weight: ["400", "500"],
});

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={`${cormorant.variable} ${dmSans.variable} ${jetbrains.variable}`}>
        <Providers>{children}</Providers>
        <Toaster
          theme="dark"
          toastOptions={{
            style: {
              background: "#111113",
              border: "1px solid #1C1C1F",
              color: "#F5F0E8",
            },
          }}
        />
      </body>
    </html>
  );
}