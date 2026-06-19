import { HelpCircle } from "lucide-react"
import type { ComponentProps, ReactNode } from "react"

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

/**
 * Small, accessible "?" affordance that reveals an explanation on hover, focus,
 * and tap. Built on the Radix tooltip so keyboard and screen-reader users get it
 * too (unlike a bare `title=""`). Use it to explain dense concepts inline.
 *
 * Keep the content non-interactive (plain text/markup) — a tooltip can't hold
 * focusable children. For rich/interactive content (e.g. links), use a Popover.
 */
export function InfoHint({
  children,
  label = "More information",
  side = "top",
  className,
  contentClassName,
}: {
  /** The explanation shown when the hint is opened. */
  children: ReactNode
  /** Accessible label for the trigger — screen readers announce this. */
  label?: string
  side?: ComponentProps<typeof TooltipContent>["side"]
  /** Extra classes for the trigger button. */
  className?: string
  /** Extra classes for the tooltip content. */
  contentClassName?: string
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={label}
          className={cn(
            "inline-flex size-4 shrink-0 items-center justify-center rounded-full align-middle text-muted-foreground/70 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            className,
          )}
        >
          <HelpCircle className="size-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent
        side={side}
        className={cn(
          "max-w-[16rem] text-pretty font-normal",
          contentClassName,
        )}
      >
        {children}
      </TooltipContent>
    </Tooltip>
  )
}
