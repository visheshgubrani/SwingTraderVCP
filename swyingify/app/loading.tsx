import { Skeleton } from "@/components/ui/skeleton"

export default function Loading() {
  return (
    <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 px-4 py-10 sm:px-6 lg:px-8">
      <Skeleton className="h-5 w-40" />
      <Skeleton className="h-14 w-full max-w-2xl" />
      <Skeleton className="h-[440px] w-full rounded-3xl" />
    </main>
  )
}
