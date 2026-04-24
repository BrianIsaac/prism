import type { Metadata, Viewport } from "next";
import { Nav } from "@/components/nav";
import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";
import { Geist } from "next/font/google";
import { cn } from "@/lib/utils";

const geist = Geist({subsets:['latin'],variable:'--font-sans'});

export const metadata: Metadata = {
  title: "Prism — agentic city discovery",
  description:
    "Three LLM agents race through GrabMaps APIs with different priors; a frozen harness scores their plans; validated picks pin to a shared Singapore canvas.",
};

export const viewport: Viewport = {
  themeColor: "#161a1d",
  colorScheme: "dark",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // h-[calc(100vh-3rem)] gives the page root a definite height (3rem = Nav's
  // h-12). CSS `height: 100%` on deeper children only resolves against an
  // ancestor with an explicit height — without this calc, the canvas container
  // collapses because every intermediate wrapper has auto-derived height.
  return (
    <html lang="en" className={cn("dark", "font-sans", geist.variable)}>
      <body className="flex flex-col overflow-hidden">
        <Nav />
        <div className="h-[calc(100vh-3rem)] overflow-hidden">{children}</div>
      </body>
    </html>
  );
}
