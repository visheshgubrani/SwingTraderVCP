import { AppHeader } from "@/components/app-header"

export default function ProductLayout({ children, detail }: { children: React.ReactNode; detail: React.ReactNode }) {
  return (
    <>
      <AppHeader />
      <main className="flex-1">{children}</main>
      {detail}
    </>
  )
}
