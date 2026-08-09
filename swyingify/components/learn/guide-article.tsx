import Link from "next/link"

import { Breadcrumbs } from "@/components/site/breadcrumbs"
import { getRelatedGuides } from "@/lib/learn/registry"
import { GUIDE_STATUS_COPY, type LearnGuide } from "@/lib/learn/types"
import { CANONICAL_SCANNER_PATH } from "@/lib/seo/config"
import { EDITORIAL, formatEditorialDate } from "@/lib/seo/editorial"
import { articleJsonLd, breadcrumbJsonLd, JsonLd } from "@/lib/seo/json-ld"

export function GuideArticle({ guide }: { guide: LearnGuide }) {
  const related = getRelatedGuides(guide)
  const crumbs = [
    { label: "Home", href: "/" },
    { label: "Learn", href: "/learn" },
    { label: guide.title },
  ]

  return (
    <>
      <JsonLd
        data={[
          articleJsonLd({
            title: guide.h1,
            description: guide.description,
            path: `/learn/${guide.slug}`,
            publishedAt: guide.publishedAt,
            reviewedAt: guide.reviewedAt,
          }),
          breadcrumbJsonLd([
            { name: "Home", path: "/" },
            { name: "Learn", path: "/learn" },
            { name: guide.title, path: `/learn/${guide.slug}` },
          ]),
        ]}
      />

      <article className="landing-block">
        <div className="landing-container max-w-[760px]">
          <Breadcrumbs items={crumbs} />

          <p className="landing-kicker mt-8">{guide.statusLabel}</p>
          <h1 className="landing-display mt-4 text-[clamp(28px,4vw,44px)]">{guide.h1}</h1>
          <p className="landing-lead mt-6">{guide.definition}</p>

          <p className="mt-6 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-wider text-[var(--landing-muted)]">
            {EDITORIAL.name} · Published {formatEditorialDate(guide.publishedAt)} · Reviewed{" "}
            {formatEditorialDate(guide.reviewedAt)}
          </p>
          <p className="mt-2 text-sm text-[var(--landing-muted)]">{EDITORIAL.disclaimer}</p>
          <p className="mt-2 text-sm text-[var(--landing-muted)]">{GUIDE_STATUS_COPY[guide.status]}</p>

          {(guide.liveScannerCta || guide.status !== "live-linked") && (
            <p className="mt-8">
              <Link href={CANONICAL_SCANNER_PATH} className="landing-btn landing-btn-primary">
                {guide.liveScannerCta ? "Open the live Minervini VCP scanner" : "Browse the live Minervini scanner"}
              </Link>
            </p>
          )}

          <SectionList heading="Practical checklist" items={guide.checklist} />
          <SectionList heading="What a scanner can measure" items={guide.screenable} />
          <SectionList heading="What still needs human judgment" items={guide.humanJudgment} />
          <SectionList heading="Common failure modes" items={guide.failureModes} />

          {guide.sections.map((section) => (
            <section key={section.id} className="mt-12" id={section.id}>
              <h2 className="landing-h2 text-[clamp(22px,3vw,30px)]">{section.heading}</h2>
              {section.paragraphs.map((paragraph) => (
                <p key={paragraph.slice(0, 24)} className="mt-4 text-base leading-relaxed text-[var(--landing-fg-2)]">
                  {paragraph}
                </p>
              ))}
              {section.bullets ? (
                <ul className="mt-4 list-disc space-y-2 pl-5 text-base leading-relaxed text-[var(--landing-fg-2)]">
                  {section.bullets.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : null}
            </section>
          ))}

          <section className="mt-12" id="sources">
            <h2 className="landing-h2 text-[clamp(22px,3vw,30px)]">Primary sources</h2>
            <ul className="mt-4 space-y-3">
              {guide.sources.map((source) => (
                <li key={source.title} className="border border-[var(--landing-border)] px-4 py-3">
                  <p className="font-[family-name:var(--font-landing-mono)] text-sm text-[var(--landing-fg)]">
                    {source.title}
                  </p>
                  <p className="mt-1 text-sm text-[var(--landing-muted)]">{source.detail}</p>
                </li>
              ))}
            </ul>
          </section>

          <section className="mt-12" id="related">
            <h2 className="landing-h2 text-[clamp(22px,3vw,30px)]">Related reading</h2>
            <ul className="mt-4 space-y-2">
              {related.map((item) => (
                <li key={item.slug}>
                  <Link href={`/learn/${item.slug}`} className="text-[var(--landing-fg)] underline-offset-4 hover:underline">
                    {item.title}
                  </Link>
                  <span className="text-[var(--landing-muted)]"> — {item.definition}</span>
                </li>
              ))}
              <li>
                <Link href="/methodology" className="text-[var(--landing-fg)] underline-offset-4 hover:underline">
                  Scanner methodology
                </Link>
              </li>
            </ul>
          </section>
        </div>
      </article>
    </>
  )
}

function SectionList({ heading, items }: { heading: string; items: string[] }) {
  return (
    <section className="mt-12">
      <h2 className="landing-h2 text-[clamp(22px,3vw,30px)]">{heading}</h2>
      <ul className="mt-4 list-disc space-y-2 pl-5 text-base leading-relaxed text-[var(--landing-fg-2)]">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  )
}
