import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

import {
  type Body_login_login_access_token as AccessToken,
  LoginService,
  type UserPublic,
  type UserRegister,
  UsersService,
} from "@/client"
import { handleError } from "@/utils"
import useCustomToast from "./useCustomToast"
import { MIGRATION_FLAG, migrateLocalBuildsToDb } from "./useLocalBuilds"

const isLoggedIn = () => {
  return localStorage.getItem("access_token") !== null
}

const useAuth = () => {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showErrorToast, showSuccessToast } = useCustomToast()

  const { data: user } = useQuery<UserPublic | null, Error>({
    queryKey: ["currentUser"],
    queryFn: UsersService.readUserMe,
    enabled: isLoggedIn(),
  })

  const signUpMutation = useMutation({
    mutationFn: (data: UserRegister) =>
      UsersService.registerUser({ requestBody: data }),
    onSuccess: () => {
      // If the user had anonymous builds, flag them for migration after login.
      if (localStorage.getItem("anon_builds")) {
        sessionStorage.setItem(MIGRATION_FLAG, "1")
      }
      navigate({ to: "/login" })
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] })
    },
  })

  const login = async (data: AccessToken) => {
    const response = await LoginService.loginAccessToken({
      formData: data,
    })
    localStorage.setItem("access_token", response.access_token)
  }

  const loginMutation = useMutation({
    mutationFn: login,
    onSuccess: async () => {
      const search = new URLSearchParams(window.location.search)
      const redirectTo = search.get("redirect") || "/"

      if (sessionStorage.getItem(MIGRATION_FLAG)) {
        sessionStorage.removeItem(MIGRATION_FLAG)
        const count = await migrateLocalBuildsToDb()
        if (count > 0) {
          showSuccessToast(`${count} build${count !== 1 ? "s" : ""} synced to your account.`)
        }
      }

      navigate({ to: redirectTo as any })
    },
    onError: handleError.bind(showErrorToast),
  })

  const logout = () => {
    localStorage.removeItem("access_token")
    navigate({ to: "/" })
  }

  return {
    signUpMutation,
    loginMutation,
    logout,
    user,
  }
}

export { isLoggedIn }
export default useAuth
