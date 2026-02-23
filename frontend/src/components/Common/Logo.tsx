import { Link } from "@tanstack/react-router"

import { cn } from "@/lib/utils"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
}

export function Logo({
  variant = "full",
  className,
  asLink = true,
}: LogoProps) {
  const content =
    variant === "responsive" ? (
      <>
        <span
          className={cn(
            "font-heading text-base font-semibold tracking-[0.18em] text-gold group-data-[collapsible=icon]:hidden",
            className,
          )}
        >
          NIGHTREIGN
        </span>
        <span
          className={cn(
            "font-heading text-sm font-semibold tracking-widest text-gold hidden group-data-[collapsible=icon]:block",
            className,
          )}
        >
          NR
        </span>
      </>
    ) : (
      <span
        className={cn(
          variant === "full"
            ? "font-heading text-base font-semibold tracking-[0.18em] text-gold"
            : "font-heading text-sm font-semibold tracking-widest text-gold",
          className,
        )}
      >
        {variant === "full" ? "NIGHTREIGN" : "NR"}
      </span>
    )

  if (!asLink) {
    return content
  }

  return <Link to="/">{content}</Link>
}
