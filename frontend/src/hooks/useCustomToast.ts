import { toast } from "sonner"

// Module-scope so the returned functions are referentially stable — consumers
// use them in hook dependency arrays (they only wrap sonner's global `toast`).
const showSuccessToast = (description: string) => {
  toast.success("Success!", {
    description,
  })
}

const showErrorToast = (description: string) => {
  toast.error("Something went wrong!", {
    description,
  })
}

const api = { showSuccessToast, showErrorToast }

const useCustomToast = () => api

export default useCustomToast
