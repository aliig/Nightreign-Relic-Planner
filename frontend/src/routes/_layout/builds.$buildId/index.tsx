import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/builds/$buildId/")({
  beforeLoad: ({ params }) => {
    throw redirect({ to: "/builds/$buildId/edit", params })
  },
})
