import { Logo } from "@/components/Common/Logo"
import { Footer } from "./Footer"

interface AuthLayoutProps {
  children: React.ReactNode
}

export function AuthLayout({ children }: AuthLayoutProps) {
  return (
    <div className="grid min-h-svh lg:grid-cols-2">
      <div
        className="relative hidden lg:flex lg:items-center lg:justify-center overflow-hidden"
        style={{ background: 'radial-gradient(ellipse at 50% 50%, oklch(0.18 0.04 250 / 80%) 0%, oklch(0.06 0.02 260) 70%)' }}
      >
        <div className="flex flex-col items-center gap-3">
          <Logo variant="full" className="text-3xl tracking-[0.25em]" asLink={false} />
          <div className="h-px w-24 bg-gradient-to-r from-transparent via-gold/40 to-transparent" />
          <p className="font-heading text-xs tracking-[0.3em] text-muted-foreground uppercase">Relic Planner</p>
        </div>
      </div>
      <div className="flex flex-col gap-4 p-6 md:p-10 bg-background">
        <div className="flex flex-1 items-center justify-center">
          <div className="w-full max-w-xs">{children}</div>
        </div>
        <Footer />
      </div>
    </div>
  )
}
