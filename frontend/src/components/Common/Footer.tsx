export function Footer() {
  const currentYear = new Date().getFullYear()

  return (
    <footer className="border-t border-border/50 py-4 px-6">
      <div className="flex flex-col items-center justify-between gap-4 sm:flex-row">
        <p className="text-muted-foreground text-xs tracking-wider">
          Nightreign Relic Planner &mdash; {currentYear}
        </p>
        <p className="font-heading text-xs tracking-widest text-gold/40">
          NIGHTREIGN
        </p>
      </div>
    </footer>
  )
}
