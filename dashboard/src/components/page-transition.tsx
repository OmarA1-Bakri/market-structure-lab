import type { ReactNode } from "react"
import { motion, useReducedMotion } from "motion/react"

const transition = {
  type: "spring",
  stiffness: 180,
  damping: 24,
} as const

export function PageTransition({ children }: { children: ReactNode }) {
  const reduceMotion = useReducedMotion()

  return (
    <motion.div
      initial={reduceMotion ? false : { opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      exit={reduceMotion ? undefined : { opacity: 0, y: -8 }}
      transition={transition}
      className="mx-auto w-full max-w-[1480px] px-4 py-6 md:px-7 md:py-8 xl:px-10"
      style={{ willChange: reduceMotion ? "auto" : "opacity, transform" }}
    >
      {children}
    </motion.div>
  )
}
