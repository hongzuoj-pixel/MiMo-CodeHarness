export function showToast(message: string): string {
  return `[Toast] ${message}`
}

export function formatError(moduleName: string, message: string): string {
  return `${moduleName}: ${message}`
}
