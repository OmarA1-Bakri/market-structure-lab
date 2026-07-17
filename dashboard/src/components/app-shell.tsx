import type { ComponentType, ReactNode } from "react"
import {
  ArchiveTrayIcon,
  ChartLineUpIcon,
  CirclesThreePlusIcon,
  DatabaseIcon,
  FileCodeIcon,
  FlaskIcon,
  ListIcon,
  LockKeyIcon,
  PulseIcon,
  SquaresFourIcon,
} from "@phosphor-icons/react"
import type { IconProps } from "@phosphor-icons/react"
import { motion, useReducedMotion } from "motion/react"

import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { evidenceTimestamp, type PageId } from "@/data/lab-data"
import { cn } from "@/lib/utils"

interface NavigationItem {
  id: PageId
  label: string
  detail: string
  icon: ComponentType<IconProps>
}

const navigation: NavigationItem[] = [
  {
    id: "overview",
    label: "Overview",
    detail: "Research gate",
    icon: SquaresFourIcon,
  },
  {
    id: "data",
    label: "Data health",
    detail: "Freshness evidence",
    icon: DatabaseIcon,
  },
  {
    id: "auction",
    label: "Auction replay",
    detail: "Deterministic fixture",
    icon: ChartLineUpIcon,
  },
  {
    id: "features",
    label: "Feature registry",
    detail: "FS-000001",
    icon: CirclesThreePlusIcon,
  },
  {
    id: "discovery",
    label: "Discovery lab",
    detail: "Golden replay",
    icon: FlaskIcon,
  },
  {
    id: "artifacts",
    label: "Artifact inspector",
    detail: "Local JSON",
    icon: FileCodeIcon,
  },
]

interface AppShellProps {
  activePage: PageId
  onNavigate: (page: PageId) => void
  children: ReactNode
}

function LabMark() {
  return (
    <div className="relative grid size-10 shrink-0 place-items-center overflow-hidden rounded-xl border border-primary/30 bg-primary/10 text-primary panel-edge">
      <span className="font-mono text-[0.68rem] font-semibold tracking-[-0.04em]">
        MSL
      </span>
      <span className="absolute inset-x-2 bottom-1 h-px bg-primary/45" />
    </div>
  )
}

function Navigation({
  activePage,
  onNavigate,
  compact = false,
}: {
  activePage: PageId
  onNavigate: (page: PageId) => void
  compact?: boolean
}) {
  const reduceMotion = useReducedMotion()

  return (
    <nav aria-label="Primary navigation" className="space-y-1">
      {navigation.map((item) => {
        const Icon = item.icon
        const active = activePage === item.id
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onNavigate(item.id)}
            aria-current={active ? "page" : undefined}
            className={cn(
              "relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors active:translate-y-px",
              active
                ? "text-foreground"
                : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
              compact && "py-3",
            )}
          >
            {active ? (
              <motion.span
                layoutId={compact ? "mobile-nav-active" : "desktop-nav-active"}
                className="absolute inset-0 rounded-xl border border-primary/18 bg-primary/[0.075]"
                transition={
                  reduceMotion
                    ? { duration: 0 }
                    : { type: "spring", stiffness: 280, damping: 28 }
                }
              />
            ) : null}
            <Icon
              size={18}
              weight={active ? "fill" : "regular"}
              className={cn("relative", active && "text-primary")}
            />
            <span className="relative min-w-0">
              <span className="block text-sm font-medium">{item.label}</span>
              <span className="block truncate font-mono text-[0.62rem] text-muted-foreground">
                {item.detail}
              </span>
            </span>
          </button>
        )
      })}
    </nav>
  )
}

export function AppShell({
  activePage,
  onNavigate,
  children,
}: AppShellProps) {
  return (
    <div className="min-h-[100dvh]">
      <aside className="fixed inset-y-0 left-0 hidden w-64 border-r border-border bg-sidebar/82 px-4 py-5 backdrop-blur-xl lg:flex lg:flex-col">
        <div className="flex items-center gap-3 px-2">
          <LabMark />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold tracking-tight">
              Market Structure Lab
            </p>
            <p className="font-mono text-[0.62rem] text-muted-foreground">
              research console / phase 04
            </p>
          </div>
        </div>

        <div className="mt-8">
          <p className="mb-3 px-3 text-[0.62rem] font-medium tracking-[0.2em] text-muted-foreground uppercase">
            Evidence surfaces
          </p>
          <Navigation activePage={activePage} onNavigate={onNavigate} />
        </div>

        <div className="mt-auto space-y-4 px-2">
          <div className="rounded-xl border border-border bg-background/35 p-3 panel-edge">
            <div className="flex items-center gap-2 text-xs text-foreground">
              <LockKeyIcon size={15} className="text-primary" />
              Phase 5 sealed
            </div>
            <p className="mt-2 text-[0.68rem] leading-relaxed text-muted-foreground">
              No outcomes, edges, strategies, positions, or P&amp;L are exposed.
            </p>
          </div>
          <div className="flex items-center gap-2 px-1 font-mono text-[0.6rem] text-muted-foreground">
            <ArchiveTrayIcon size={13} />
            <span className="truncate">Evidence {evidenceTimestamp}</span>
          </div>
        </div>
      </aside>

      <div className="lg:pl-64">
        <header className="sticky top-0 z-40 flex h-16 items-center justify-between border-b border-border bg-background/78 px-4 backdrop-blur-xl md:px-7 lg:px-10">
          <div className="flex items-center gap-3 lg:hidden">
            <Sheet>
              <SheetTrigger asChild>
                <Button
                  variant="outline"
                  size="icon"
                  aria-label="Open navigation"
                  className="border-border bg-background/50"
                >
                  <ListIcon size={18} />
                </Button>
              </SheetTrigger>
              <SheetContent
                side="left"
                className="w-[min(88vw,22rem)] border-r-border bg-background/96 p-5"
              >
                <SheetHeader className="text-left">
                  <SheetTitle className="flex items-center gap-3">
                    <LabMark />
                    Market Structure Lab
                  </SheetTitle>
                  <SheetDescription>
                    Read-only evidence console. Phase 5 remains sealed.
                  </SheetDescription>
                </SheetHeader>
                <div className="mt-7">
                  <Navigation
                    activePage={activePage}
                    onNavigate={onNavigate}
                    compact
                  />
                </div>
              </SheetContent>
            </Sheet>
            <p className="text-sm font-semibold tracking-tight">MSL Console</p>
          </div>

          <div className="hidden items-center gap-2 lg:flex">
            <PulseIcon size={16} className="text-primary" />
            <span className="font-mono text-[0.68rem] text-muted-foreground">
              verified software · live-data maintenance · no live trading
            </span>
          </div>

          <Tooltip>
            <TooltipTrigger asChild>
              <div className="flex cursor-default items-center gap-2 rounded-full border border-primary/20 bg-primary/[0.07] px-3 py-1.5">
                <motion.span
                  animate={{ opacity: [0.45, 1, 0.45] }}
                  transition={{ duration: 2.8, repeat: Number.POSITIVE_INFINITY }}
                  className="size-1.5 rounded-full bg-primary"
                  style={{ willChange: "opacity" }}
                />
                <span className="font-mono text-[0.64rem] text-primary">
                  evidence current
                </span>
              </div>
            </TooltipTrigger>
            <TooltipContent sideOffset={8}>
              Snapshot content is pinned to {evidenceTimestamp}
            </TooltipContent>
          </Tooltip>
        </header>

        <main>{children}</main>
      </div>
    </div>
  )
}
