import type { MetadataRoute } from "next"

import { absoluteUrl, isSeoIndexingEnabled } from "@/lib/seo/config"
import { ROBOTS_DISALLOW } from "@/lib/seo/routes"

export const dynamic = "force-dynamic"

export default function robots(): MetadataRoute.Robots {
  if (!isSeoIndexingEnabled()) {
    return {
      rules: {
        userAgent: "*",
        disallow: "/",
      },
    }
  }

  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: [...ROBOTS_DISALLOW],
    },
    sitemap: absoluteUrl("/sitemap.xml"),
  }
}
