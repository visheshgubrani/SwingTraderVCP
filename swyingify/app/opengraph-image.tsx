import { ImageResponse } from "next/og"

import { SITE_NAME, SITE_TAGLINE } from "@/lib/seo/config"

export const size = { width: 1200, height: 630 }
export const contentType = "image/png"

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: 72,
          background: "linear-gradient(145deg, #0c0f12 0%, #161b22 55%, #1a222c 100%)",
          color: "#f3f1ec",
          fontFamily: "ui-sans-serif, system-ui, sans-serif",
        }}
      >
        <div style={{ display: "flex", fontSize: 28, letterSpacing: 6, textTransform: "uppercase", opacity: 0.7 }}>
          {SITE_NAME}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 18, maxWidth: 900 }}>
          <div style={{ fontSize: 58, lineHeight: 1.1, fontWeight: 600 }}>{SITE_TAGLINE}</div>
          <div style={{ fontSize: 26, lineHeight: 1.4, opacity: 0.78 }}>
            Independent Minervini VCP approximation for the Nifty 500 · Educational only
          </div>
        </div>
      </div>
    ),
    { ...size },
  )
}
