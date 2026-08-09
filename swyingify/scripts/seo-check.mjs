#!/usr/bin/env node
/**
 * Dependency-free SEO sanity checks for Swyingify.
 * Run: pnpm seo:check
 */

import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, "..")
const errors = []

function fail(message) {
  errors.push(message)
}

function read(rel) {
  return fs.readFileSync(path.join(root, rel), "utf8")
}

function extractExportArray(source, exportName) {
  const re = new RegExp(`export const ${exportName} = \\[([\\s\\S]*?)\\] as const`)
  const match = source.match(re)
  if (!match) {
    fail(`Could not parse ${exportName}`)
    return []
  }
  return [...match[1].matchAll(/"([^"]+)"/g)].map((m) => m[1])
}

function extractGuideField(source, field) {
  const re = new RegExp(`${field}:\\s*"((?:\\\\.|[^"\\\\])*)"`)
  const match = source.match(re)
  return match ? match[1] : null
}

function extractRelatedSlugs(source) {
  const match = source.match(/relatedSlugs:\s*\[([\s\S]*?)\],/)
  if (!match) return []
  return [...match[1].matchAll(/"([^"]+)"/g)].map((m) => m[1])
}

function extractSourcesCount(source) {
  const match = source.match(/sources:\s*\[([\s\S]*?)\],\s*relatedSlugs/)
  if (!match) return 0
  return [...match[1].matchAll(/title:\s*"/g)].length
}

const slugs = extractExportArray(read("lib/learn/slugs.ts"), "LEARN_GUIDE_SLUGS")
if (slugs.length !== 9) fail(`Expected 9 learn guide slugs, found ${slugs.length}`)

const guideDir = path.join(root, "lib/learn/guides")
const guideFiles = fs.readdirSync(guideDir).filter((f) => f.endsWith(".ts"))
if (guideFiles.length !== slugs.length) {
  fail(`Guide file count ${guideFiles.length} != slug count ${slugs.length}`)
}

const titles = new Set()
const descriptions = new Set()
const metaTitles = new Set()
const slugSet = new Set(slugs)

for (const file of guideFiles) {
  const source = fs.readFileSync(path.join(guideDir, file), "utf8")
  const slug = extractGuideField(source, "slug")
  const title = extractGuideField(source, "title")
  const metaTitle = extractGuideField(source, "metaTitle")
  const description = extractGuideField(source, "description")
  const status = extractGuideField(source, "status")
  const publishedAt = extractGuideField(source, "publishedAt")
  const reviewedAt = extractGuideField(source, "reviewedAt")

  if (!slug || !slugSet.has(slug)) fail(`${file}: missing/unknown slug`)
  if (!title) fail(`${file}: missing title`)
  if (!metaTitle) fail(`${file}: missing metaTitle`)
  if (!description) fail(`${file}: missing description`)
  if (!status) fail(`${file}: missing status`)
  if (!publishedAt || !reviewedAt) fail(`${file}: missing editorial dates`)

  if (titles.has(title)) fail(`Duplicate guide title: ${title}`)
  titles.add(title)
  if (metaTitles.has(metaTitle)) fail(`Duplicate metaTitle: ${metaTitle}`)
  metaTitles.add(metaTitle)
  if (descriptions.has(description)) fail(`Duplicate description: ${description}`)
  descriptions.add(description)

  const related = extractRelatedSlugs(source)
  if (!related.length) fail(`${file}: relatedSlugs empty`)
  for (const relatedSlug of related) {
    if (!slugSet.has(relatedSlug)) fail(`${file}: related slug missing from registry: ${relatedSlug}`)
    if (relatedSlug === slug) fail(`${file}: related slug self-reference`)
  }

  if (extractSourcesCount(source) < 1) fail(`${file}: needs at least one source`)
}

const routesSource = read("lib/seo/routes.ts")
if (!routesSource.includes("LEARN_GUIDE_SLUGS")) {
  fail("lib/seo/routes.ts must derive learn paths from LEARN_GUIDE_SLUGS")
}

const requiredPaths = [
  "/",
  "/scanners",
  "/scanners/minervini-vcp",
  "/learn",
  "/about",
  "/methodology",
  "/disclaimer",
  ...slugs.map((s) => `/learn/${s}`),
]

if (!routesSource.includes('path: "/"') || !routesSource.includes("CANONICAL_SCANNER_PATH")) {
  fail("INDEXABLE_ROUTES missing core marketing paths")
}

const excluded = ["/api", "/sign-in", "/sign-up", "/stocks"]
for (const prefix of excluded) {
  if (!routesSource.includes(prefix)) fail(`Missing sitemap exclusion for ${prefix}`)
}

const nextConfig = read("next.config.ts")
if (!nextConfig.includes('source: "/scanner"') || !nextConfig.includes('destination: "/scanners/minervini-vcp"')) {
  fail("next.config.ts must permanently redirect /scanner to /scanners/minervini-vcp")
}
if (!nextConfig.includes('source: "/scanners/minervini"')) {
  fail("next.config.ts must redirect /scanners/minervini")
}
if (!nextConfig.includes("permanent: true")) {
  fail("Scanner redirects must be permanent: true")
}

for (const file of ["app/sign-in/page.tsx", "app/sign-up/page.tsx", "app/stocks/[symbol]/page.tsx"]) {
  const source = read(file)
  if (!source.includes("noIndex: true") && !source.includes("index: false")) {
    fail(`${file} must set noindex`)
  }
}

const walk = (dir, out = []) => {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === ".next") continue
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) walk(full, out)
    else if (/\.(tsx|ts|jsx|js)$/.test(entry.name)) out.push(full)
  }
  return out
}

const hrefScanner = /href=["'`]\/scanner["'`]/
for (const file of walk(root)) {
  const rel = path.relative(root, file)
  if (rel === "next.config.ts" || rel.startsWith("scripts/")) continue
  if (rel === "lib/seo/routes.ts") continue
  const source = fs.readFileSync(file, "utf8")
  if (hrefScanner.test(source) || /href=["']\/scanner["']/.test(source)) {
    fail(`${rel} still links to /scanner — use /scanners/minervini-vcp`)
  }
}

if (!fs.existsSync(path.join(root, "app/robots.ts"))) fail("Missing app/robots.ts")
if (!fs.existsSync(path.join(root, "app/sitemap.ts"))) fail("Missing app/sitemap.ts")
if (!fs.existsSync(path.join(root, "app/scanners/minervini-vcp/page.tsx"))) {
  fail("Missing canonical scanner page")
}
if (fs.existsSync(path.join(root, "app/scanner/page.tsx"))) {
  fail("Legacy app/scanner/page.tsx should be removed (redirect only)")
}

const marketingPages = [
  "app/page.tsx",
  "app/scanners/page.tsx",
  "app/learn/page.tsx",
  "app/about/page.tsx",
  "app/methodology/page.tsx",
  "app/disclaimer/page.tsx",
  "app/scanners/minervini-vcp/page.tsx",
]
const pageTitles = new Set()
for (const file of marketingPages) {
  const source = read(file)
  const match = source.match(/title:\s*"((?:\\.|[^"\\])*)"/)
  if (!match) {
    fail(`${file}: could not find metadata title`)
    continue
  }
  if (pageTitles.has(match[1])) fail(`Duplicate page title: ${match[1]}`)
  pageTitles.add(match[1])
}

if (errors.length) {
  console.error("seo:check failed:\n")
  for (const error of errors) console.error(` - ${error}`)
  process.exit(1)
}

console.log(
  `seo:check passed (${slugs.length} guides, ${requiredPaths.length} expected indexable paths when indexing enabled).`,
)
