"use client"

import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { useState } from "react"
import { LogOutIcon, MenuIcon, ScanLineIcon, UserRoundIcon, XIcon } from "lucide-react"

import { BrandMark } from "@/components/brand-mark"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { authClient } from "@/lib/auth-client"

export function AppHeader() {
  const pathname = usePathname()
  const router = useRouter()
  const [mobileOpen, setMobileOpen] = useState(false)
  const { data: session, isPending } = authClient.useSession()
  const user = session?.user

  async function handleSignOut() {
    await authClient.signOut()
    router.push("/")
    router.refresh()
  }

  return (
    <header className="sticky top-0 z-40 border-b border-border/70 bg-background/90 backdrop-blur-xl">
      <div className="mx-auto flex h-16 w-full max-w-7xl items-center justify-between gap-5 px-4 sm:px-6 lg:px-8">
        <div className="flex items-center gap-7">
          <BrandMark />
          <nav className="hidden items-center gap-1 md:flex" aria-label="Primary navigation">
            <Link
              href="/scanner"
              className={`inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors hover:bg-muted ${pathname.startsWith("/scanner") ? "bg-muted text-foreground" : "text-muted-foreground"}`}
            >
              <ScanLineIcon data-icon="inline-start" />
              Scanners
            </Link>
            <Link
              href="/#scan"
              className="rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              The scan
            </Link>
          </nav>
        </div>
        <div className="flex items-center gap-2">
          <Link href="/scanner" className="hidden text-sm font-medium text-muted-foreground hover:text-foreground sm:inline-flex">
            Explore the scan
          </Link>
          {!isPending && user ? (
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button variant="ghost" size="icon-sm" aria-label="Open account menu">
                    <Avatar className="size-7">
                      <AvatarFallback>{user.name?.slice(0, 1).toUpperCase() || "S"}</AvatarFallback>
                    </Avatar>
                  </Button>
                }
              />
              <DropdownMenuContent align="end" className="w-52">
                <div className="px-2 py-1.5">
                  <p className="truncate text-sm font-medium">{user.name}</p>
                  <p className="truncate text-xs text-muted-foreground">{user.email}</p>
                </div>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={handleSignOut}>
                  <LogOutIcon data-icon="inline-start" />
                  Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <Button nativeButton={false} render={<Link href="/sign-in" />} size="sm">
              <UserRoundIcon data-icon="inline-start" />
              Sign in
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon-sm"
            className="md:hidden"
            aria-label={mobileOpen ? "Close menu" : "Open menu"}
            aria-expanded={mobileOpen}
            aria-controls="mobile-navigation"
            onClick={() => setMobileOpen((open) => !open)}
          >
            {mobileOpen ? <XIcon /> : <MenuIcon />}
          </Button>
        </div>
      </div>
      {mobileOpen && (
        <nav id="mobile-navigation" className="border-t border-border/70 bg-background px-4 py-3 md:hidden" aria-label="Mobile navigation">
          <div className="mx-auto flex w-full max-w-7xl flex-col gap-1">
            <Link href="/scanner" onClick={() => setMobileOpen(false)} className="rounded-lg px-3 py-2.5 text-sm font-medium hover:bg-muted">
              <span className="flex items-center gap-2"><ScanLineIcon data-icon="inline-start" />Scanners</span>
            </Link>
            <Link href="/#scan" onClick={() => setMobileOpen(false)} className="rounded-lg px-3 py-2.5 text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground">
              The scan
            </Link>
            <Link href="/scanner" onClick={() => setMobileOpen(false)} className="mt-1 rounded-lg bg-primary/10 px-3 py-2.5 text-sm font-medium text-primary">
              Explore the scan
            </Link>
          </div>
        </nav>
      )}
    </header>
  )
}
