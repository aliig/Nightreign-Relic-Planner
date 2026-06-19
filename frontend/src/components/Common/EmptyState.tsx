import type { LucideIcon } from "lucide-react"
import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

/**
 * Centered empty-state block: icon, title, explanatory body, and an optional
 * call-to-action. Use wherever a list/section has no data yet, so the moment
 * teaches the user what the thing is and what to do next — instead of a bare
 * "Nothing here" line.
 */
export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
  className,
}: {
  icon?: LucideIcon
  title: string
  /** Explanatory body — keep it short and concrete. */
  children?: ReactNode
  /** Optional CTA (e.g. a Button/Link). */
  action?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed px-6 py-12 text-center",
        className,
      )}
    >
      {Icon && (
        <div className="flex size-11 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Icon className="size-5" />
        </div>
      )}
      <div className="space-y-1">
        <h3 className="font-medium">{title}</h3>
        {children && (
          <div className="mx-auto max-w-sm text-pretty text-sm text-muted-foreground">
            {children}
          </div>
        )}
      </div>
      {action && <div className="pt-1">{action}</div>}
    </div>
  )
}
