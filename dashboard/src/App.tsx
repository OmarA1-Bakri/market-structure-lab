import { lazy, Suspense, useCallback, useEffect, useState } from "react"
import { AnimatePresence, MotionConfig } from "motion/react"

import { AppShell } from "@/components/app-shell"
import { PageTransition } from "@/components/page-transition"
import { Skeleton } from "@/components/ui/skeleton"
import { TooltipProvider } from "@/components/ui/tooltip"
import type { PageId } from "@/data/lab-data"
import { OverviewPage } from "@/pages/overview-page"

const DataHealthPage = lazy(() =>
  import("@/pages/data-health-page").then((module) => ({
    default: module.DataHealthPage,
  })),
)
const AuctionReplayPage = lazy(() =>
  import("@/pages/auction-replay-page").then((module) => ({
    default: module.AuctionReplayPage,
  })),
)
const FeatureRegistryPage = lazy(() =>
  import("@/pages/feature-registry-page").then((module) => ({
    default: module.FeatureRegistryPage,
  })),
)
const DiscoveryPage = lazy(() =>
  import("@/pages/discovery-page").then((module) => ({
    default: module.DiscoveryPage,
  })),
)
const ArtifactInspectorPage = lazy(() =>
  import("@/pages/artifact-inspector-page").then((module) => ({
    default: module.ArtifactInspectorPage,
  })),
)

const titles: Record<PageId, string> = {
  overview: "Overview",
  data: "Data Health",
  auction: "Auction Replay",
  features: "Feature Registry",
  discovery: "Discovery Lab",
  artifacts: "Artifact Inspector",
}

const validPages = new Set<PageId>(Object.keys(titles) as PageId[])

function pageFromHash(): PageId {
  const page = window.location.hash.replace(/^#\/?/, "") as PageId
  return validPages.has(page) ? page : "overview"
}

function Page({
  activePage,
  onNavigate,
}: {
  activePage: PageId
  onNavigate: (page: PageId) => void
}) {
  switch (activePage) {
    case "overview":
      return <OverviewPage onNavigate={onNavigate} />
    case "data":
      return <DataHealthPage />
    case "auction":
      return <AuctionReplayPage />
    case "features":
      return <FeatureRegistryPage />
    case "discovery":
      return <DiscoveryPage />
    case "artifacts":
      return <ArtifactInspectorPage />
  }
}

function PageLoading() {
  return (
    <div className="mx-auto w-full max-w-[1480px] space-y-8 px-4 py-6 md:px-7 md:py-8 xl:px-10">
      <div className="space-y-4 border-b border-border pb-8">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-14 w-full max-w-3xl" />
        <Skeleton className="h-20 w-full max-w-2xl" />
      </div>
      <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((item) => (
          <Skeleton key={item} className="h-28" />
        ))}
      </div>
      <Skeleton className="h-96 w-full rounded-[1.7rem]" />
    </div>
  )
}

export default function App() {
  const [activePage, setActivePage] = useState<PageId>(pageFromHash)

  const navigate = useCallback((page: PageId) => {
    setActivePage(page)
    window.location.hash = page === "overview" ? "" : page
  }, [])

  useEffect(() => {
    const handleHashChange = () => setActivePage(pageFromHash())
    window.addEventListener("hashchange", handleHashChange)
    return () => window.removeEventListener("hashchange", handleHashChange)
  }, [])

  useEffect(() => {
    document.title = `${titles[activePage]} · Market Structure Lab`
    window.scrollTo({ top: 0, behavior: "smooth" })
  }, [activePage])

  return (
    <MotionConfig reducedMotion="user">
      <TooltipProvider delayDuration={280}>
        <AppShell activePage={activePage} onNavigate={navigate}>
          <AnimatePresence mode="wait">
            <Suspense key={activePage} fallback={<PageLoading />}>
              <PageTransition>
                <Page activePage={activePage} onNavigate={navigate} />
              </PageTransition>
            </Suspense>
          </AnimatePresence>
        </AppShell>
      </TooltipProvider>
    </MotionConfig>
  )
}
