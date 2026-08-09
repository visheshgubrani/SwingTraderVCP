import type { MetadataRoute } from "next"

import { absoluteUrl, isSeoIndexingEnabled } from "@/lib/seo/config"
import { INDEXABLE_ROUTES } from "@/lib/seo/routes"

export const dynamic = "force-dynamic"

export default function sitemap(): MetadataRoute.Sitemap {
  if (!isSeoIndexingEnabled()) {
    return []
  }

  return INDEXABLE_ROUTES.map((route) => ({
    url: absoluteUrl(route.path),
    lastModified: route.lastModified ? new Date(route.lastModified) : new Date(),
    changeFrequency: route.changeFrequency,
    priority: route.priority,
  }))
}
